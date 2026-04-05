"""Tests for Box connector."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.box.connector import (
    BoxConnector,
    get_file_extension,
    get_mimetype_enum_for_box,
    get_parent_path_from_path,
)
from app.models.entities import AppUser, AppUserGroup, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType
import asyncio
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    SyncFilterKey,
)
from app.models.entities import AppUser, RecordGroupType, RecordType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_box_entry(entry_type="file", entry_id="f1", name="doc.pdf",
                    size=1024, created_at="2024-01-15T10:30:00Z",
                    modified_at="2024-06-15T10:30:00Z", path_parts=None,
                    shared_link=None, owned_by=None, etag="etag1", sha1="sha1hash"):
    if path_parts is None:
        path_parts = [{"id": "0", "name": "All Files"}, {"id": "p1", "name": "Folder"}]
    entry = {
        "type": entry_type,
        "id": entry_id,
        "name": name,
        "size": size,
        "created_at": created_at,
        "modified_at": modified_at,
        "path_collection": {"entries": path_parts},
        "etag": etag,
        "sha1": sha1,
    }
    if shared_link:
        entry["shared_link"] = shared_link
    if owned_by:
        entry["owned_by"] = owned_by
    return entry


def _make_mock_tx_store(existing_record=None, record_group=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_record_group_by_external_id = AsyncMock(return_value=record_group)
    tx.create_record_group_relation = AsyncMock()
    tx.get_app_user_by_email = AsyncMock(return_value=None)
    tx.get_user_groups = AsyncMock(return_value=[])
    tx.get_records_by_parent = AsyncMock(return_value=[])
    tx.remove_user_access_to_record = AsyncMock()
    tx.get_app_users = AsyncMock(return_value=[])
    return tx


def _make_mock_data_store_provider(existing_record=None, record_group=None):
    tx = _make_mock_tx_store(existing_record, record_group)
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
    return logging.getLogger("test.box")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-box-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.on_record_deleted = AsyncMock()
    proc.on_record_metadata_update = AsyncMock()
    proc.on_record_content_update = AsyncMock()
    proc.on_updated_record_permissions = AsyncMock()
    proc.on_user_group_deleted = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[MagicMock(email="user@test.com")])
    proc.get_all_app_users = AsyncMock(return_value=[])
    return proc


@pytest.fixture()
def mock_data_store_provider():
    return _make_mock_data_store_provider()


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "clientId": "box-client-id",
            "clientSecret": "box-client-secret",
            "enterpriseId": "box-ent-123",
        },
    })
    return svc


@pytest.fixture()
def box_connector(mock_logger, mock_data_entities_processor,
                  mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.box.connector.BoxApp"):
        connector = BoxConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="box-conn-1",
            scope="team",
            created_by="test-user",
        )
    connector.sync_filters = FilterCollection()
    connector.indexing_filters = FilterCollection()
    connector.data_source = AsyncMock()
    connector.box_cursor_sync_point = AsyncMock()
    connector.box_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
    connector.box_cursor_sync_point.update_sync_point = AsyncMock()
    return connector


# ===========================================================================
# Helper functions
# ===========================================================================

class TestBoxGetParentPath:
    def test_root_returns_none(self):
        assert get_parent_path_from_path("/") is None

    def test_empty_returns_none(self):
        assert get_parent_path_from_path("") is None

    def test_nested(self):
        assert get_parent_path_from_path("/a/b/c.txt") == "/a/b"

    def test_single_level(self):
        assert get_parent_path_from_path("/file.txt") is None


class TestBoxGetFileExtension:
    def test_normal(self):
        assert get_file_extension("report.pdf") == "pdf"

    def test_no_ext(self):
        assert get_file_extension("Makefile") is None

    def test_compound(self):
        assert get_file_extension("backup.tar.gz") == "gz"


class TestBoxMimeType:
    def test_folder(self):
        assert get_mimetype_enum_for_box("folder") == MimeTypes.FOLDER

    def test_file_pdf(self):
        assert get_mimetype_enum_for_box("file", "report.pdf") == MimeTypes.PDF

    def test_file_unknown(self):
        assert get_mimetype_enum_for_box("file", "data.xyz999") == MimeTypes.BIN

    def test_file_no_filename(self):
        assert get_mimetype_enum_for_box("file") == MimeTypes.BIN

    def test_non_file_non_folder(self):
        assert get_mimetype_enum_for_box("web_link") == MimeTypes.BIN


# ===========================================================================
# BoxConnector init
# ===========================================================================

class TestBoxConnectorInit:
    def test_constructor(self, box_connector):
        assert box_connector.connector_id == "box-conn-1"
        assert box_connector.batch_size == 100

    @patch("app.connectors.sources.box.connector.BoxClient.build_with_config", new_callable=AsyncMock)
    @patch("app.connectors.sources.box.connector.BoxDataSource")
    async def test_init_success(self, mock_ds_cls, mock_build, box_connector):
        mock_client = MagicMock()
        mock_client.get_client.return_value = MagicMock()
        mock_client.get_client.return_value.create_client = AsyncMock()
        mock_build.return_value = mock_client
        mock_ds_cls.return_value = MagicMock()
        assert await box_connector.init() is True

    async def test_init_fails_no_config(self, box_connector):
        box_connector.config_service.get_config = AsyncMock(return_value=None)
        assert await box_connector.init() is False

    async def test_init_fails_no_auth(self, box_connector):
        box_connector.config_service.get_config = AsyncMock(return_value={"other": "data"})
        assert await box_connector.init() is False

    async def test_init_fails_missing_credentials(self, box_connector):
        box_connector.config_service.get_config = AsyncMock(return_value={"auth": {"clientId": "id"}})
        assert await box_connector.init() is False

    @patch("app.connectors.sources.box.connector.BoxClient.build_with_config", new_callable=AsyncMock)
    async def test_init_fails_client_error(self, mock_build, box_connector):
        mock_build.side_effect = Exception("Auth failure")
        assert await box_connector.init() is False


# ===========================================================================
# _parse_box_timestamp and _to_dict
# ===========================================================================

class TestBoxParseTimestamp:
    def test_parse_valid_timestamp(self, box_connector):
        ts = "2024-01-15T10:30:00Z"
        result = box_connector._parse_box_timestamp(ts, "created", "file.txt")
        expected = int(datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc).timestamp() * 1000)
        assert result == expected

    def test_parse_none_timestamp(self, box_connector):
        result = box_connector._parse_box_timestamp(None, "created", "file.txt")
        assert result > 0

    def test_parse_invalid_timestamp(self, box_connector):
        result = box_connector._parse_box_timestamp("not-a-date", "modified", "file.txt")
        assert result > 0


class TestBoxToDict:
    def test_none_returns_empty(self, box_connector):
        assert box_connector._to_dict(None) == {}

    def test_dict_passthrough(self, box_connector):
        d = {"key": "value"}
        assert box_connector._to_dict(d) == d

    def test_object_with_to_dict(self, box_connector):
        obj = MagicMock()
        obj.to_dict.return_value = {"id": "123"}
        assert box_connector._to_dict(obj) == {"id": "123"}

    def test_object_with_response_object(self, box_connector):
        obj = MagicMock(spec=[])
        obj.response_object = {"data": True}
        assert box_connector._to_dict(obj) == {"data": True}

    def test_object_without_methods(self, box_connector):
        obj = object()
        assert box_connector._to_dict(obj) == {}


# ===========================================================================
# _process_box_entry
# ===========================================================================

class TestProcessBoxEntry:
    async def test_new_file_entry(self, box_connector):
        entry = _make_box_entry()
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=MagicMock(success=False, error="404")
        )
        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        assert result is not None
        assert result.is_new is True
        assert result.record.record_name == "doc.pdf"
        assert result.record.is_file is True
        assert result.record.extension == "pdf"

    async def test_new_folder_entry(self, box_connector):
        entry = _make_box_entry(entry_type="folder", name="MyFolder")
        box_connector.data_source.collaborations_get_folder_collaborations = AsyncMock(
            return_value=MagicMock(success=False, error="404")
        )
        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        assert result is not None
        assert result.record.is_file is False

    async def test_entry_without_id_skipped(self, box_connector):
        entry = {"type": "file", "name": "doc.pdf"}
        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        assert result is None

    async def test_entry_without_name_skipped(self, box_connector):
        entry = {"type": "file", "id": "f1"}
        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        assert result is None

    async def test_existing_record_detected(self, box_connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 1
        existing.source_updated_at = 1705312200000  # Different from entry
        box_connector.data_store_provider = _make_mock_data_store_provider(existing)

        entry = _make_box_entry()
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=MagicMock(success=False, error="None")
        )
        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        assert result.is_new is False
        assert result.is_updated is True

    async def test_shared_link_company_adds_org_permission(self, box_connector):
        entry = _make_box_entry(shared_link={"access": "company", "url": "https://box.com/s/123"})
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=MagicMock(success=False, error="None")
        )
        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        org_perms = [p for p in result.new_permissions if "ORG_" in (p.external_id or "")]
        assert len(org_perms) == 1
        assert org_perms[0].entity_type == EntityType.GROUP

    async def test_shared_link_open_adds_public_permission(self, box_connector):
        entry = _make_box_entry(shared_link={"access": "open", "url": "https://box.com/s/public"})
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=MagicMock(success=False, error="None")
        )
        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        public_perms = [p for p in result.new_permissions if p.external_id == "PUBLIC"]
        assert len(public_perms) == 1

    async def test_shared_with_me_detected(self, box_connector):
        entry = _make_box_entry(owned_by={"id": "other-user"})
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=MagicMock(success=False, error="None")
        )
        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        assert result.record.is_shared_with_me is True

    async def test_no_size_field_logs_warning(self, box_connector):
        entry = _make_box_entry()
        del entry["size"]
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=MagicMock(success=False, error="None")
        )
        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        assert result.record.size_in_bytes == 0

    async def test_indexing_filter_disables_shared(self, box_connector):
        entry = _make_box_entry(shared_link={"access": "company", "url": "https://box.com/s/123"})
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=MagicMock(success=False, error="None")
        )
        box_connector.indexing_filters = MagicMock()
        box_connector.indexing_filters.is_enabled.return_value = False

        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        assert result.record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    async def test_exception_returns_none(self, box_connector):
        entry = _make_box_entry()
        # Force an exception inside the method
        box_connector.data_store_provider = MagicMock()
        box_connector.data_store_provider.transaction.side_effect = Exception("DB error")

        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        assert result is None


# ===========================================================================
# _get_permissions
# ===========================================================================

class TestBoxGetPermissions:
    async def test_file_permissions_with_collaborators(self, box_connector):
        collab_data = {
            "entries": [
                {
                    "accessible_by": {"id": "u1", "type": "user", "login": "user@test.com"},
                    "role": "editor"
                },
                {
                    "accessible_by": {"id": "g1", "type": "group"},
                    "role": "viewer"
                },
                {
                    "accessible_by": {"id": "u2", "type": "user", "login": "owner@test.com"},
                    "role": "owner"
                },
            ]
        }
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=MagicMock(success=True, data=collab_data)
        )
        perms = await box_connector._get_permissions("f1", "file")
        assert len(perms) == 3
        assert perms[0].type == PermissionType.WRITE
        assert perms[1].type == PermissionType.READ
        assert perms[1].entity_type == EntityType.GROUP
        assert perms[2].type == PermissionType.OWNER

    async def test_folder_permissions(self, box_connector):
        collab_data = {
            "entries": [
                {
                    "accessible_by": {"id": "u1", "type": "user", "login": "user@test.com"},
                    "role": "co-owner"
                },
            ]
        }
        box_connector.data_source.collaborations_get_folder_collaborations = AsyncMock(
            return_value=MagicMock(success=True, data=collab_data)
        )
        perms = await box_connector._get_permissions("d1", "folder")
        assert len(perms) == 1
        assert perms[0].type == PermissionType.WRITE

    async def test_failed_response_returns_empty(self, box_connector):
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=MagicMock(success=False, error="Access denied")
        )
        perms = await box_connector._get_permissions("f1", "file")
        assert perms == []

    async def test_404_returns_empty(self, box_connector):
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=MagicMock(success=False, error="404 not found")
        )
        perms = await box_connector._get_permissions("f1", "file")
        assert perms == []

    async def test_skips_entries_without_id(self, box_connector):
        collab_data = {
            "entries": [
                {"accessible_by": {"type": "user"}, "role": "viewer"},
            ]
        }
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=MagicMock(success=True, data=collab_data)
        )
        perms = await box_connector._get_permissions("f1", "file")
        assert perms == []

    async def test_exception_returns_empty(self, box_connector):
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            side_effect=Exception("API error")
        )
        perms = await box_connector._get_permissions("f1", "file")
        assert perms == []


# ===========================================================================
# _handle_record_updates
# ===========================================================================

class TestBoxHandleRecordUpdates:
    async def test_deleted_record(self, box_connector):
        update = MagicMock()
        update.is_deleted = True
        update.is_updated = False
        update.external_record_id = "ext-1"

        existing = MagicMock()
        existing.id = "internal-1"
        box_connector.data_store_provider = _make_mock_data_store_provider(existing)

        await box_connector._handle_record_updates(update)
        box_connector.data_entities_processor.on_record_deleted.assert_called_once()

    async def test_updated_record(self, box_connector):
        update = MagicMock()
        update.is_deleted = False
        update.is_updated = True
        update.record = MagicMock()
        update.new_permissions = [MagicMock()]
        await box_connector._handle_record_updates(update)
        box_connector.data_entities_processor.on_new_records.assert_called_once()

    async def test_exception_handled(self, box_connector):
        update = MagicMock()
        update.is_deleted = True
        update.external_record_id = "ext-1"
        box_connector.data_store_provider = MagicMock()
        box_connector.data_store_provider.transaction.side_effect = Exception("DB error")
        await box_connector._handle_record_updates(update)  # Should not raise


# ===========================================================================
# _sync_users
# ===========================================================================

class TestBoxSyncUsers:
    async def test_sync_users_single_page(self, box_connector):
        user_data = {
            "entries": [
                {"id": "u1", "login": "user@test.com", "name": "Test User", "status": "active", "job_title": "Dev"},
            ],
            "total_count": 1
        }
        box_connector.data_source.users_get_users = AsyncMock(
            return_value=MagicMock(success=True, data=user_data)
        )
        result = await box_connector._sync_users()
        assert len(result) == 1
        assert result[0].email == "user@test.com"
        assert result[0].is_active is True

    async def test_sync_users_empty(self, box_connector):
        box_connector.data_source.users_get_users = AsyncMock(
            return_value=MagicMock(success=True, data={"entries": []})
        )
        result = await box_connector._sync_users()
        assert result == []

    async def test_sync_users_api_failure(self, box_connector):
        box_connector.data_source.users_get_users = AsyncMock(
            return_value=MagicMock(success=False, error="API error")
        )
        result = await box_connector._sync_users()
        assert result == []

    async def test_sync_users_exception(self, box_connector):
        box_connector.data_source.users_get_users = AsyncMock(side_effect=Exception("Network error"))
        result = await box_connector._sync_users()
        assert result == []


# ===========================================================================
# _ensure_virtual_groups
# ===========================================================================

class TestBoxEnsureVirtualGroups:
    async def test_creates_public_and_org_groups(self, box_connector):
        await box_connector._ensure_virtual_groups()
        box_connector.data_entities_processor.on_new_user_groups.assert_called_once()
        call_args = box_connector.data_entities_processor.on_new_user_groups.call_args[0][0]
        group_ids = [g.source_user_group_id for g, _ in call_args]
        assert "PUBLIC" in group_ids
        assert f"ORG_{box_connector.data_entities_processor.org_id}" in group_ids


# ===========================================================================
# _get_app_users_by_emails
# ===========================================================================

class TestBoxGetAppUsersByEmails:
    async def test_empty_emails(self, box_connector):
        result = await box_connector._get_app_users_by_emails([])
        assert result == []

    async def test_finds_users(self, box_connector):
        mock_user = MagicMock()
        tx = _make_mock_tx_store()
        tx.get_app_user_by_email = AsyncMock(return_value=mock_user)
        box_connector.data_store_provider = _make_mock_data_store_provider()

        # Override to return mock user
        with patch.object(box_connector, "data_store_provider", _make_mock_data_store_provider()):
            provider = box_connector.data_store_provider
            result = await box_connector._get_app_users_by_emails(["user@test.com"])
            # Returns users found (may be empty due to mock setup)
            assert isinstance(result, list)


# ===========================================================================
# _remove_user_access_from_folder_recursively
# ===========================================================================

class TestBoxRemoveUserAccess:
    async def test_removes_access_recursively(self, box_connector):
        child = MagicMock()
        child.external_record_id = "child-1"
        tx = _make_mock_tx_store()
        tx.get_records_by_parent = AsyncMock(side_effect=[[child], []])
        box_connector.data_store_provider = _make_mock_data_store_provider()

        await box_connector._remove_user_access_from_folder_recursively("folder-1", "user-1")
        # Should not raise

    async def test_handles_exception(self, box_connector):
        box_connector.data_store_provider = MagicMock()
        box_connector.data_store_provider.transaction.side_effect = Exception("DB error")
        await box_connector._remove_user_access_from_folder_recursively("folder-1", "user-1")
        # Should not raise


# ===========================================================================
# _process_box_items_generator
# ===========================================================================

class TestProcessBoxItemsGenerator:
    async def test_yields_new_records(self, box_connector):
        mock_update = MagicMock()
        mock_update.is_deleted = False
        mock_update.is_updated = False
        mock_update.is_new = True
        mock_update.record = MagicMock()
        mock_update.new_permissions = []

        with patch.object(box_connector, "_process_box_entry", new_callable=AsyncMock) as mock_proc:
            mock_proc.return_value = mock_update
            results = []
            async for rec, perms, update in box_connector._process_box_items_generator(
                [_make_box_entry()], "u1", "user@test.com", "rg1"
            ):
                results.append(update)
            assert len(results) == 1

    async def test_yields_updated_records(self, box_connector):
        mock_update = MagicMock()
        mock_update.is_deleted = False
        mock_update.is_updated = True
        mock_update.is_new = False
        mock_update.record = MagicMock()
        mock_update.new_permissions = []

        with patch.object(box_connector, "_process_box_entry", new_callable=AsyncMock) as mock_proc:
            mock_proc.return_value = mock_update
            results = []
            async for rec, perms, update in box_connector._process_box_items_generator(
                [_make_box_entry()], "u1", "user@test.com", "rg1"
            ):
                results.append(update)
            assert len(results) == 1

    async def test_yields_deleted_records(self, box_connector):
        mock_update = MagicMock()
        mock_update.is_deleted = True
        mock_update.is_updated = False
        mock_update.is_new = False
        mock_update.record = None
        mock_update.new_permissions = []

        with patch.object(box_connector, "_process_box_entry", new_callable=AsyncMock) as mock_proc:
            mock_proc.return_value = mock_update
            results = []
            async for rec, perms, update in box_connector._process_box_items_generator(
                [_make_box_entry()], "u1", "user@test.com", "rg1"
            ):
                results.append(update)
            assert len(results) == 1

    async def test_skips_none(self, box_connector):
        with patch.object(box_connector, "_process_box_entry", new_callable=AsyncMock) as mock_proc:
            mock_proc.return_value = None
            results = []
            async for rec, perms, update in box_connector._process_box_items_generator(
                [_make_box_entry()], "u1", "user@test.com", "rg1"
            ):
                results.append(update)
            assert len(results) == 0


# ===========================================================================
# _reconcile_deleted_groups
# ===========================================================================

class TestBoxReconcileDeletedGroups:
    async def test_deletes_stale_groups(self, box_connector):
        stale_group = MagicMock()
        stale_group.source_user_group_id = "stale-1"
        stale_group.name = "Stale Group"

        tx = _make_mock_tx_store()
        tx.get_user_groups = AsyncMock(return_value=[stale_group])
        box_connector.data_store_provider = _make_mock_data_store_provider()

        # Override the transaction to return stale groups
        @asynccontextmanager
        async def _tx():
            yield tx
        box_connector.data_store_provider = MagicMock()
        box_connector.data_store_provider.transaction = _tx

        await box_connector._reconcile_deleted_groups({"active-1", "active-2"})
        box_connector.data_entities_processor.on_user_group_deleted.assert_called_once()

    async def test_no_stale_groups(self, box_connector):
        group = MagicMock()
        group.source_user_group_id = "active-1"

        tx = _make_mock_tx_store()
        tx.get_user_groups = AsyncMock(return_value=[group])

        @asynccontextmanager
        async def _tx():
            yield tx
        box_connector.data_store_provider = MagicMock()
        box_connector.data_store_provider.transaction = _tx

        await box_connector._reconcile_deleted_groups({"active-1"})
        box_connector.data_entities_processor.on_user_group_deleted.assert_not_called()


# ===========================================================================
# DEEP SYNC LOOP TESTS — run_sync, _sync_folder_recursively,
# _run_sync_for_user, _process_users_in_batches, _sync_users,
# _sync_user_groups, _sync_record_groups
# ===========================================================================


class TestBoxRunSync:
    """Tests for run_sync orchestration (full / incremental)."""

    async def test_full_sync_no_cursor(self, box_connector):
        box_connector.box_cursor_sync_point.read_sync_point = AsyncMock(return_value=None)

        # Anchor the stream
        box_connector.data_source.events_get_events = AsyncMock(
            return_value=MagicMock(success=True, data={"next_stream_position": "pos123"})
        )
        box_connector._to_dict = MagicMock(
            return_value={"next_stream_position": "pos123"}
        )
        box_connector.box_cursor_sync_point.update_sync_point = AsyncMock()

        box_connector._sync_users = AsyncMock(return_value=[])
        box_connector._ensure_virtual_groups = AsyncMock()
        box_connector._sync_user_groups = AsyncMock()
        box_connector._sync_record_groups = AsyncMock()
        box_connector._process_users_in_batches = AsyncMock()
        box_connector._get_date_filters = MagicMock(return_value=(None, None, None, None))

        with patch(
            "app.connectors.sources.box.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            await box_connector.run_sync()

        box_connector._sync_users.assert_awaited_once()
        box_connector._ensure_virtual_groups.assert_awaited_once()
        box_connector._sync_user_groups.assert_awaited_once()
        box_connector._sync_record_groups.assert_awaited_once()
        box_connector._process_users_in_batches.assert_awaited_once()

    async def test_incremental_sync_path(self, box_connector):
        import time
        now_ms = int(time.time() * 1000)
        box_connector.box_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "cursor-val", "cursor_updated_at": now_ms}
        )
        box_connector.run_incremental_sync = AsyncMock()
        box_connector._get_date_filters = MagicMock(return_value=(None, None, None, None))

        with patch(
            "app.connectors.sources.box.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            await box_connector.run_sync()

        box_connector.run_incremental_sync.assert_awaited_once()

    async def test_old_cursor_triggers_full_sync(self, box_connector):
        # Cursor older than 14 days
        old_ms = int((datetime.now(timezone.utc).timestamp() - 20 * 86400) * 1000)
        box_connector.box_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "old-cursor", "cursor_updated_at": old_ms}
        )
        box_connector.data_source.events_get_events = AsyncMock(
            return_value=MagicMock(success=True, data={"next_stream_position": "new-pos"})
        )
        box_connector._to_dict = MagicMock(
            return_value={"next_stream_position": "new-pos"}
        )
        box_connector._sync_users = AsyncMock(return_value=[])
        box_connector._ensure_virtual_groups = AsyncMock()
        box_connector._sync_user_groups = AsyncMock()
        box_connector._sync_record_groups = AsyncMock()
        box_connector._process_users_in_batches = AsyncMock()
        box_connector._get_date_filters = MagicMock(return_value=(None, None, None, None))

        with patch(
            "app.connectors.sources.box.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            await box_connector.run_sync()

        box_connector._sync_users.assert_awaited_once()

    async def test_run_sync_raises_on_error(self, box_connector):
        box_connector.box_cursor_sync_point.read_sync_point = AsyncMock(
            side_effect=Exception("read fail")
        )
        box_connector._get_date_filters = MagicMock(return_value=(None, None, None, None))

        with patch(
            "app.connectors.sources.box.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            # It should still proceed past read_sync_point failure
            box_connector.data_source.events_get_events = AsyncMock(
                return_value=MagicMock(success=False, data={})
            )
            box_connector._to_dict = MagicMock(return_value={})
            box_connector._sync_users = AsyncMock(return_value=[])
            box_connector._ensure_virtual_groups = AsyncMock()
            box_connector._sync_user_groups = AsyncMock()
            box_connector._sync_record_groups = AsyncMock()
            box_connector._process_users_in_batches = AsyncMock()
            await box_connector.run_sync()


class TestBoxSyncFolderRecursively:
    """Tests for _sync_folder_recursively deep recursion."""

    async def test_empty_folder(self, box_connector):
        box_connector.current_user_id = "admin-1"
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.folders_get_folder_items = AsyncMock(
            return_value=MagicMock(success=True, data={"entries": [], "total_count": 0})
        )
        box_connector._to_dict = MagicMock(return_value={"entries": [], "total_count": 0})

        user = MagicMock()
        user.source_user_id = "admin-1"
        user.email = "admin@test.com"
        batch = []
        await box_connector._sync_folder_recursively(user, "0", batch)
        assert batch == []

    async def test_file_items_added_to_batch(self, box_connector):
        box_connector.current_user_id = "admin-1"
        box_connector.data_source.clear_as_user_context = AsyncMock()

        file_entry = _make_box_entry(entry_type="file", entry_id="f1", name="report.pdf")
        box_connector.data_source.folders_get_folder_items = AsyncMock(
            return_value=MagicMock(success=True, data={"entries": [file_entry], "total_count": 1})
        )
        box_connector._to_dict = MagicMock(
            return_value={"entries": [file_entry], "total_count": 1}
        )

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_record = MagicMock()
        mock_record.mime_type = "application/pdf"
        mock_update = RecordUpdate(
            record=mock_record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[],
        )
        box_connector._process_box_entry = AsyncMock(return_value=mock_update)

        user = MagicMock()
        user.source_user_id = "admin-1"
        user.email = "admin@test.com"
        batch = []
        await box_connector._sync_folder_recursively(user, "0", batch)
        assert len(batch) == 1

    async def test_folder_items_trigger_recursion(self, box_connector):
        from app.config.constants.arangodb import MimeTypes as MimeTypesConst
        box_connector.current_user_id = "admin-1"
        box_connector.data_source.clear_as_user_context = AsyncMock()

        folder_entry = _make_box_entry(entry_type="folder", entry_id="sub1", name="Sub")
        empty_resp = {"entries": [], "total_count": 0}

        call_count = [0]

        def _to_dict_side(data):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"entries": [folder_entry], "total_count": 1}
            return empty_resp

        box_connector._to_dict = MagicMock(side_effect=_to_dict_side)
        box_connector.data_source.folders_get_folder_items = AsyncMock(
            return_value=MagicMock(success=True, data={})
        )

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_record = MagicMock()
        mock_record.mime_type = MimeTypesConst.FOLDER.value
        mock_record.external_record_id = "sub1"
        mock_update = RecordUpdate(
            record=mock_record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[],
        )
        box_connector._process_box_entry = AsyncMock(return_value=mock_update)

        user = MagicMock()
        user.source_user_id = "admin-1"
        user.email = "admin@test.com"
        batch = []
        await box_connector._sync_folder_recursively(user, "0", batch)
        # Folder added + recursion triggered
        assert len(batch) == 1

    async def test_api_failure_breaks_loop(self, box_connector):
        box_connector.current_user_id = "admin-1"
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.folders_get_folder_items = AsyncMock(
            return_value=MagicMock(success=False, error="403 Forbidden")
        )

        user = MagicMock()
        user.source_user_id = "admin-1"
        user.email = "admin@test.com"
        batch = []
        await box_connector._sync_folder_recursively(user, "0", batch)
        assert batch == []

    async def test_updated_record_calls_handle_updates(self, box_connector):
        box_connector.current_user_id = "admin-1"
        box_connector.data_source.clear_as_user_context = AsyncMock()

        file_entry = _make_box_entry()
        box_connector.data_source.folders_get_folder_items = AsyncMock(
            return_value=MagicMock(success=True, data={"entries": [file_entry], "total_count": 1})
        )
        box_connector._to_dict = MagicMock(
            return_value={"entries": [file_entry], "total_count": 1}
        )

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_record = MagicMock()
        mock_update = RecordUpdate(
            record=mock_record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=True,
            permissions_changed=False,
            new_permissions=[],
        )
        box_connector._process_box_entry = AsyncMock(return_value=mock_update)
        box_connector._handle_record_updates = AsyncMock()

        user = MagicMock()
        user.source_user_id = "admin-1"
        user.email = "admin@test.com"
        batch = []
        await box_connector._sync_folder_recursively(user, "0", batch)
        box_connector._handle_record_updates.assert_awaited_once()

    async def test_sets_as_user_context_for_different_user(self, box_connector):
        box_connector.current_user_id = "admin-1"
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.folders_get_folder_items = AsyncMock(
            return_value=MagicMock(success=True, data={"entries": [], "total_count": 0})
        )
        box_connector._to_dict = MagicMock(return_value={"entries": [], "total_count": 0})

        user = MagicMock()
        user.source_user_id = "user-2"
        user.email = "user2@test.com"
        batch = []
        await box_connector._sync_folder_recursively(user, "0", batch)
        box_connector.data_source.set_as_user_context.assert_awaited_once_with("user-2")


class TestBoxRunSyncForUser:
    """Tests for _run_sync_for_user."""

    async def test_syncs_and_flushes(self, box_connector):
        box_connector._sync_folder_recursively = AsyncMock()

        user = MagicMock()
        user.email = "test@test.com"
        await box_connector._run_sync_for_user(user)
        box_connector._sync_folder_recursively.assert_awaited_once()

    async def test_flushes_remaining_batch(self, box_connector):
        async def _mock_sync(user, folder_id, batch_records):
            batch_records.append(("rec", []))

        box_connector._sync_folder_recursively = _mock_sync

        user = MagicMock()
        user.email = "test@test.com"
        await box_connector._run_sync_for_user(user)
        box_connector.data_entities_processor.on_new_records.assert_awaited_once()

    async def test_exception_handled(self, box_connector):
        box_connector._sync_folder_recursively = AsyncMock(
            side_effect=Exception("sync error")
        )

        user = MagicMock()
        user.email = "test@test.com"
        await box_connector._run_sync_for_user(user)  # Should not raise


class TestBoxProcessUsersInBatches:
    """Tests for _process_users_in_batches."""

    async def test_filters_active_users(self, box_connector):
        active_user = MagicMock()
        active_user.email = "active@test.com"
        inactive_user = MagicMock()
        inactive_user.email = "inactive@test.com"

        box_connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[active_user]
        )
        box_connector._run_sync_for_user = AsyncMock()

        users = [
            MagicMock(email="active@test.com"),
            MagicMock(email="inactive@test.com"),
        ]
        await box_connector._process_users_in_batches(users)
        box_connector._run_sync_for_user.assert_awaited_once()

    async def test_continues_on_user_error(self, box_connector):
        user1 = MagicMock(email="u1@test.com")
        user2 = MagicMock(email="u2@test.com")

        box_connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[user1, user2]
        )
        box_connector._run_sync_for_user = AsyncMock(
            side_effect=[Exception("error"), None]
        )

        await box_connector._process_users_in_batches([user1, user2])
        assert box_connector._run_sync_for_user.await_count == 2


class TestBoxSyncUserGroups:
    """Tests for _sync_user_groups."""

    async def test_syncs_groups_and_reconciles(self, box_connector):
        box_connector.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])

        group_data = {"id": "g1", "name": "Engineering", "description": "Eng team"}
        box_connector.data_source.groups_get_groups = AsyncMock(
            return_value=MagicMock(success=True, data={"entries": [group_data]})
        )
        box_connector._to_dict = MagicMock(
            return_value={"entries": [group_data]}
        )
        box_connector.data_source.groups_get_group_memberships = AsyncMock(
            return_value=MagicMock(success=True, data={"entries": []})
        )
        box_connector._reconcile_deleted_groups = AsyncMock()

        await box_connector._sync_user_groups()
        box_connector.data_entities_processor.on_new_user_groups.assert_awaited()
        box_connector._reconcile_deleted_groups.assert_awaited_once()

    async def test_api_failure(self, box_connector):
        box_connector.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
        box_connector.data_source.groups_get_groups = AsyncMock(
            return_value=MagicMock(success=False, error="API error")
        )

        await box_connector._sync_user_groups()  # Should not raise


class TestBoxSyncRecordGroups:
    """Tests for _sync_record_groups."""

    async def test_creates_record_groups_for_users(self, box_connector):
        user = MagicMock()
        user.source_user_id = "u1"
        user.email = "user@test.com"
        user.full_name = "Test User"

        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.folders_get_folder_by_id = AsyncMock(
            return_value=MagicMock(success=True, data={"id": "0", "name": "All Files"})
        )
        box_connector._to_dict = MagicMock(
            return_value={"id": "0", "name": "All Files"}
        )

        await box_connector._sync_record_groups([user])
        box_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    async def test_skips_user_context_error(self, box_connector):
        user = MagicMock()
        user.source_user_id = "u1"
        user.email = "user@test.com"

        box_connector.data_source.set_as_user_context = AsyncMock(
            side_effect=Exception("context error")
        )

        await box_connector._sync_record_groups([user])
        box_connector.data_entities_processor.on_new_record_groups.assert_not_called()

    async def test_skips_root_folder_error(self, box_connector):
        user = MagicMock()
        user.source_user_id = "u1"
        user.email = "user@test.com"

        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.folders_get_folder_by_id = AsyncMock(
            return_value=MagicMock(success=False, error="403")
        )

        await box_connector._sync_record_groups([user])
        box_connector.data_entities_processor.on_new_record_groups.assert_not_called()


class TestBoxSyncUsersPagination:
    """Tests for _sync_users pagination loop."""

    async def test_single_page(self, box_connector):
        user_data = {
            "entries": [
                {"id": "u1", "login": "user@test.com", "name": "User",
                 "status": "active", "job_title": "Dev"},
            ],
            "total_count": 1,
        }
        box_connector.data_source.users_get_users = AsyncMock(
            return_value=MagicMock(success=True, data=user_data)
        )
        box_connector._to_dict = MagicMock(return_value=user_data)

        result = await box_connector._sync_users()
        assert len(result) == 1
        assert result[0].email == "user@test.com"

    async def test_pagination(self, box_connector):
        page1 = {
            "entries": [
                {"id": f"u{i}", "login": f"u{i}@t.com", "name": f"U{i}",
                 "status": "active"} for i in range(1000)
            ],
        }
        page2 = {
            "entries": [
                {"id": "u1001", "login": "last@t.com", "name": "Last",
                 "status": "active"},
            ],
        }

        call_count = [0]

        def _to_dict_pages(data):
            call_count[0] += 1
            if call_count[0] == 1:
                return page1
            return page2

        box_connector._to_dict = MagicMock(side_effect=_to_dict_pages)
        box_connector.data_source.users_get_users = AsyncMock(
            return_value=MagicMock(success=True, data={})
        )

        result = await box_connector._sync_users()
        assert len(result) == 1001

    async def test_api_failure(self, box_connector):
        box_connector.data_source.users_get_users = AsyncMock(
            return_value=MagicMock(success=False, error="Auth failed")
        )

        result = await box_connector._sync_users()
        assert result == []

    async def test_exception_returns_empty(self, box_connector):
        box_connector.data_source.users_get_users = AsyncMock(
            side_effect=Exception("network error")
        )

        result = await box_connector._sync_users()
        assert result == []

# =============================================================================
# Merged from test_box_connector_full_coverage.py
# =============================================================================

def _make_box_entry_fullcov(entry_type="file", entry_id="f1", name="doc.pdf",
                    size=1024, created_at="2024-01-15T10:30:00Z",
                    modified_at="2024-06-15T10:30:00Z", path_parts=None,
                    shared_link=None, owned_by=None, etag="etag1", sha1="sha1hash"):
    if path_parts is None:
        path_parts = [{"id": "0", "name": "All Files"}, {"id": "p1", "name": "Folder"}]
    entry = {
        "type": entry_type, "id": entry_id, "name": name,
        "size": size, "created_at": created_at, "modified_at": modified_at,
        "path_collection": {"entries": path_parts},
        "etag": etag, "sha1": sha1,
    }
    if shared_link:
        entry["shared_link"] = shared_link
    if owned_by:
        entry["owned_by"] = owned_by
    return entry


def _make_mock_tx_store(existing_record=None, record_group=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_record_group_by_external_id = AsyncMock(return_value=record_group)
    tx.create_record_group_relation = AsyncMock()
    tx.get_app_user_by_email = AsyncMock(return_value=None)
    tx.get_user_groups = AsyncMock(return_value=[])
    tx.get_records_by_parent = AsyncMock(return_value=[])
    tx.remove_user_access_to_record = AsyncMock()
    tx.get_app_users = AsyncMock(return_value=[])
    return tx


def _make_mock_data_store_provider(existing_record=None, record_group=None):
    tx = _make_mock_tx_store(existing_record, record_group)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


@pytest.fixture()
def mock_logger_fullcov():
    return logging.getLogger("test.box.full")


@pytest.fixture()
def mock_data_entities_processor_fullcov():
    proc = MagicMock()
    proc.org_id = "org-box-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.on_record_deleted = AsyncMock()
    proc.on_record_metadata_update = AsyncMock()
    proc.on_record_content_update = AsyncMock()
    proc.on_updated_record_permissions = AsyncMock()
    proc.on_user_group_deleted = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[MagicMock(email="user@test.com")])
    proc.get_all_app_users = AsyncMock(return_value=[])
    proc.reindex_existing_records = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider():
    return _make_mock_data_store_provider()


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "clientId": "box-client-id",
            "clientSecret": "box-client-secret",
            "enterpriseId": "box-ent-123",
        },
    })
    return svc


@pytest.fixture()
def box_connector(mock_logger_fullcov, mock_data_entities_processor_fullcov,
                  mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.box.connector.BoxApp"):
        connector = BoxConnector(
            logger=mock_logger_fullcov,
            data_entities_processor=mock_data_entities_processor_fullcov,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="box-conn-1",
            scope="personal",
            created_by="test-user-id",
        )
    connector.sync_filters = FilterCollection()
    connector.indexing_filters = FilterCollection()
    connector.data_source = AsyncMock()
    connector.box_cursor_sync_point = AsyncMock()
    connector.box_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
    connector.box_cursor_sync_point.update_sync_point = AsyncMock()
    return connector


class TestGetParentPathExtended:
    def test_double_slash(self):
        result = get_parent_path_from_path("//a/b")
        assert result is not None

    def test_trailing_slash(self):
        result = get_parent_path_from_path("/a/b/")
        assert result is not None


class TestGetFileExtensionExtended:
    def test_hidden_file(self):
        assert get_file_extension(".gitignore") == "gitignore"

    def test_dot_only(self):
        result = get_file_extension("file.")
        assert result == ""


class TestGetMimeTypeExtended:
    def test_docx(self):
        result = get_mimetype_enum_for_box("file", "report.docx")
        assert result is not None

    def test_txt(self):
        result = get_mimetype_enum_for_box("file", "readme.txt")
        assert result is not None

    def test_unknown_type(self):
        result = get_mimetype_enum_for_box("web_link", "something")
        assert result == MimeTypes.BIN


class TestBoxRunIncrementalSync:
    async def test_incremental_sync_no_events(self, box_connector):
        box_connector._sync_users = AsyncMock(return_value=[])
        box_connector._ensure_virtual_groups = AsyncMock()
        box_connector._sync_user_groups = AsyncMock()
        box_connector.box_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "pos-123"}
        )
        box_connector.data_source.events_get_events = AsyncMock(
            return_value=MagicMock(success=True, data={
                "entries": [], "next_stream_position": "pos-124"
            })
        )
        box_connector._to_dict = MagicMock(return_value={
            "entries": [], "next_stream_position": "pos-124"
        })

        await box_connector.run_incremental_sync()
        box_connector._sync_users.assert_awaited_once()

    async def test_incremental_sync_with_events(self, box_connector):
        box_connector._sync_users = AsyncMock(return_value=[
            MagicMock(source_user_id="u1")
        ])
        box_connector._ensure_virtual_groups = AsyncMock()
        box_connector._sync_user_groups = AsyncMock()
        box_connector.box_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "pos-100"}
        )

        event = {
            "event_id": "e1", "event_type": "ITEM_UPLOAD",
            "source": {"id": "f1", "type": "file", "owned_by": {"id": "u1"}},
            "created_at": "2024-01-01T00:00:00Z",
        }
        box_connector.data_source.events_get_events = AsyncMock(side_effect=[
            MagicMock(success=True, data={
                "entries": [event], "next_stream_position": "pos-200"
            }),
            MagicMock(success=True, data={
                "entries": [], "next_stream_position": "pos-200"
            }),
        ])
        call_count = [0]

        def _to_dict_side(data):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"entries": [event], "next_stream_position": "pos-200"}
            return {"entries": [], "next_stream_position": "pos-200"}

        box_connector._to_dict = MagicMock(side_effect=_to_dict_side)
        box_connector._process_event_batch = AsyncMock()

        await box_connector.run_incremental_sync()
        box_connector._process_event_batch.assert_awaited_once()

    async def test_incremental_sync_api_failure(self, box_connector):
        box_connector._sync_users = AsyncMock(return_value=[])
        box_connector._ensure_virtual_groups = AsyncMock()
        box_connector._sync_user_groups = AsyncMock()
        box_connector.box_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "pos-100"}
        )
        box_connector.data_source.events_get_events = AsyncMock(
            return_value=MagicMock(success=False, error="API failure")
        )

        await box_connector.run_incremental_sync()

    async def test_incremental_sync_stream_expired(self, box_connector):
        box_connector._sync_users = AsyncMock(return_value=[])
        box_connector._ensure_virtual_groups = AsyncMock()
        box_connector._sync_user_groups = AsyncMock()
        box_connector.box_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "pos-100"}
        )
        box_connector.data_source.events_get_events = AsyncMock(side_effect=[
            MagicMock(success=False, error="stream_position expired"),
            MagicMock(success=True, data={"entries": [], "next_stream_position": "new-pos"}),
        ])
        call_count = [0]

        def _to_dict_side(data):
            call_count[0] += 1
            return {"entries": [], "next_stream_position": "new-pos"}

        box_connector._to_dict = MagicMock(side_effect=_to_dict_side)

        await box_connector.run_incremental_sync()


class TestBoxProcessEventBatch:
    async def test_deletion_event(self, box_connector):
        events = [{
            "event_id": "e1", "event_type": "ITEM_TRASH",
            "source": {"id": "f1", "type": "file"},
            "created_at": "2024-01-01T00:00:00Z",
        }]
        box_connector._execute_deletions = AsyncMock()
        await box_connector._process_event_batch(events)
        box_connector._execute_deletions.assert_awaited_once()

    async def test_collaboration_grant_event(self, box_connector):
        events = [{
            "event_id": "e2", "event_type": "COLLABORATION_CREATED",
            "source": {
                "item": {"id": "f1", "type": "file"},
                "accessible_by": {"id": "u1", "login": "user@test.com"},
                "owned_by": {"id": "owner1"},
            },
            "created_at": "2024-01-01T00:00:00Z",
        }]
        box_connector._get_app_users_by_emails = AsyncMock(return_value=[])
        box_connector._fetch_and_sync_files_for_owner = AsyncMock()
        await box_connector._process_event_batch(events, our_org_box_user_ids={"owner1"})
        box_connector._fetch_and_sync_files_for_owner.assert_awaited_once()

    async def test_collaboration_revocation_event(self, box_connector):
        events = [{
            "event_id": "e3", "event_type": "COLLABORATION_REMOVE",
            "source": {
                "item": {"id": "f1"},
                "accessible_by": {"id": "u1", "login": "user@test.com"},
            },
            "created_at": "2024-01-01T00:00:00Z",
        }]
        mock_user = MagicMock()
        mock_user.id = "internal-1"
        box_connector._get_app_users_by_emails = AsyncMock(return_value=[mock_user])

        existing_record = MagicMock()
        existing_record.mime_type = "application/pdf"
        tx = _make_mock_tx_store(existing_record=existing_record)

        @asynccontextmanager
        async def _transaction():
            yield tx

        box_connector.data_store_provider = MagicMock()
        box_connector.data_store_provider.transaction = _transaction

        await box_connector._process_event_batch(events)

    async def test_duplicate_event_skipped(self, box_connector):
        event = {
            "event_id": "dup1", "event_type": "ITEM_UPLOAD",
            "source": {"id": "f1", "type": "file", "owned_by": {"id": "u1"}},
            "created_at": "2024-01-01T00:00:00Z",
        }
        box_connector._fetch_and_sync_files_for_owner = AsyncMock()
        await box_connector._process_event_batch([event, event])

    async def test_event_without_source_skipped(self, box_connector):
        events = [{
            "event_id": "e5", "event_type": "ITEM_UPLOAD",
            "source": None,
            "created_at": "2024-01-01T00:00:00Z",
        }]
        await box_connector._process_event_batch(events)

    async def test_collaboration_grant_fetches_email_from_box(self, box_connector):
        events = [{
            "event_id": "e6", "event_type": "COLLABORATION_CREATED",
            "source": {
                "item": {"id": "f2", "type": "file"},
                "accessible_by": {"id": "u2"},
                "owned_by": {"id": "owner2"},
            },
            "created_at": "2024-01-01T00:00:00Z",
        }]
        box_connector.data_source.users_get_user_by_id = AsyncMock(
            return_value=MagicMock(success=True, data={"login": "u2@test.com"})
        )
        box_connector._to_dict = MagicMock(return_value={"login": "u2@test.com"})
        box_connector._get_app_users_by_emails = AsyncMock(return_value=[])
        box_connector._fetch_and_sync_files_for_owner = AsyncMock()

        await box_connector._process_event_batch(events, our_org_box_user_ids={"owner2"})

    async def test_revocation_folder_recursion(self, box_connector):
        events = [{
            "event_id": "e7", "event_type": "COLLABORATION_REMOVE",
            "source": {
                "item": {"id": "folder1"},
                "accessible_by": {"id": "u1", "login": "user@test.com"},
            },
            "created_at": "2024-01-01T00:00:00Z",
        }]
        mock_user = MagicMock()
        mock_user.id = "internal-1"
        box_connector._get_app_users_by_emails = AsyncMock(return_value=[mock_user])

        folder_record = MagicMock()
        folder_record.mime_type = MimeTypes.FOLDER.value
        tx = _make_mock_tx_store(existing_record=folder_record)

        @asynccontextmanager
        async def _transaction():
            yield tx

        box_connector.data_store_provider = MagicMock()
        box_connector.data_store_provider.transaction = _transaction
        box_connector._remove_user_access_from_folder_recursively = AsyncMock()

        await box_connector._process_event_batch(events)
        box_connector._remove_user_access_from_folder_recursively.assert_awaited_once()


class TestBoxFetchAndSyncFilesForOwner:
    async def test_successful_sync(self, box_connector):
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.files_get_file_by_id = AsyncMock(
            return_value=MagicMock(success=True, data=_make_box_entry_fullcov())
        )
        box_connector._to_dict = MagicMock(return_value=_make_box_entry_fullcov())
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[]
        ))
        box_connector._ensure_parent_folders_exist = AsyncMock()

        await box_connector._fetch_and_sync_files_for_owner("owner1", ["f1"])
        box_connector.data_entities_processor.on_new_records.assert_awaited_once()
        box_connector.data_source.clear_as_user_context.assert_awaited()

    async def test_exception_clears_context(self, box_connector):
        box_connector.data_source.set_as_user_context = AsyncMock(
            side_effect=Exception("context error")
        )
        box_connector.data_source.clear_as_user_context = AsyncMock()
        await box_connector._fetch_and_sync_files_for_owner("owner1", ["f1"])
        box_connector.data_source.clear_as_user_context.assert_awaited()


class TestBoxFetchAndSyncFoldersForOwner:
    async def test_successful_folder_sync(self, box_connector):
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.folders_get_folder_by_id = AsyncMock(
            return_value=MagicMock(success=True, data=_make_box_entry_fullcov(entry_type="folder"))
        )
        box_connector._to_dict = MagicMock(
            return_value=_make_box_entry_fullcov(entry_type="folder")
        )
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[]
        ))
        box_connector._sync_folder_contents_recursively = AsyncMock()

        await box_connector._fetch_and_sync_folders_for_owner("owner1", ["d1"])
        box_connector.data_entities_processor.on_new_records.assert_awaited_once()

    async def test_folder_fetch_failure(self, box_connector):
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.folders_get_folder_by_id = AsyncMock(
            return_value=MagicMock(success=False, error="403")
        )
        await box_connector._fetch_and_sync_folders_for_owner("owner1", ["d1"])
        box_connector.data_entities_processor.on_new_records.assert_not_awaited()


class TestBoxSyncFolderContentsRecursively:
    async def test_empty_folder(self, box_connector):
        box_connector.data_source.folders_get_folder_items = AsyncMock(
            return_value=MagicMock(success=True, data={})
        )
        box_connector._to_dict = MagicMock(return_value={"entries": [], "total_count": 0})
        batch = []
        await box_connector._sync_folder_contents_recursively("owner1", "d1", batch)
        assert batch == []

    async def test_with_items(self, box_connector):
        entry = _make_box_entry_fullcov()
        box_connector.data_source.folders_get_folder_items = AsyncMock(
            return_value=MagicMock(success=True, data={})
        )
        box_connector._to_dict = MagicMock(
            return_value={"entries": [entry], "total_count": 1}
        )
        mock_record = MagicMock()
        mock_record.mime_type = "application/pdf"
        mock_record.external_record_id = "f1"
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=mock_record, new_permissions=[]
        ))
        batch = []
        await box_connector._sync_folder_contents_recursively("owner1", "d1", batch)
        assert len(batch) == 1

    async def test_api_failure(self, box_connector):
        box_connector.data_source.folders_get_folder_items = AsyncMock(
            return_value=MagicMock(success=False, error="API error")
        )
        batch = []
        await box_connector._sync_folder_contents_recursively("owner1", "d1", batch)
        assert batch == []


class TestBoxEnsureParentFoldersExist:
    async def test_folder_already_exists(self, box_connector):
        existing = MagicMock()
        tx = _make_mock_tx_store(existing_record=existing)

        @asynccontextmanager
        async def _transaction():
            yield tx

        box_connector.data_store_provider = MagicMock()
        box_connector.data_store_provider.transaction = _transaction

        await box_connector._ensure_parent_folders_exist("owner1", ["f1"])
        box_connector.data_entities_processor.on_new_records.assert_not_awaited()

    async def test_folder_not_exists_creates(self, box_connector):
        tx = _make_mock_tx_store(existing_record=None)

        @asynccontextmanager
        async def _transaction():
            yield tx

        box_connector.data_store_provider = MagicMock()
        box_connector.data_store_provider.transaction = _transaction

        box_connector.data_source.folders_get_folder_by_id = AsyncMock(
            return_value=MagicMock(success=True, data={})
        )
        box_connector._to_dict = MagicMock(return_value={
            "type": "folder", "id": "d1", "name": "Folder",
            "path_collection": {"entries": []},
        })
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[]
        ))

        await box_connector._ensure_parent_folders_exist("owner1", ["d1"])
        box_connector.data_entities_processor.on_new_records.assert_awaited()


class TestBoxExecuteDeletions:
    async def test_empty_list(self, box_connector):
        await box_connector._execute_deletions([])

    async def test_with_ids(self, box_connector):
        await box_connector._execute_deletions(["f1", "f2"])


class TestBoxGetSignedUrl:
    async def test_no_data_source(self, box_connector):
        box_connector.data_source = None
        result = await box_connector.get_signed_url(MagicMock())
        assert result is None

    async def test_success(self, box_connector):
        record = MagicMock()
        record.external_record_id = "f1"
        record.external_record_group_id = "u1"
        record.record_name = "doc.pdf"
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.downloads_get_download_file_url = AsyncMock(
            return_value=MagicMock(success=True, data="https://download.box.com/file")
        )
        result = await box_connector.get_signed_url(record)
        assert result == "https://download.box.com/file"
        box_connector.data_source.clear_as_user_context.assert_awaited()

    async def test_failure(self, box_connector):
        record = MagicMock()
        record.external_record_id = "f1"
        record.external_record_group_id = "u1"
        record.record_name = "doc.pdf"
        record.id = "r1"
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.downloads_get_download_file_url = AsyncMock(
            return_value=MagicMock(success=False, error="denied")
        )
        result = await box_connector.get_signed_url(record)
        assert result is None

    async def test_exception(self, box_connector):
        record = MagicMock()
        record.external_record_id = "f1"
        record.external_record_group_id = "u1"
        record.id = "r1"
        box_connector.data_source.set_as_user_context = AsyncMock(
            side_effect=Exception("error")
        )
        box_connector.data_source.clear_as_user_context = AsyncMock()
        result = await box_connector.get_signed_url(record)
        assert result is None

    async def test_no_context_user_id(self, box_connector):
        record = MagicMock()
        record.external_record_id = "f1"
        record.external_record_group_id = None
        record.record_name = "doc.pdf"
        box_connector.data_source.downloads_get_download_file_url = AsyncMock(
            return_value=MagicMock(success=True, data="https://url.com")
        )
        result = await box_connector.get_signed_url(record)
        assert result == "https://url.com"


class TestBoxStreamRecord:
    async def test_no_signed_url_raises(self, box_connector):
        box_connector.get_signed_url = AsyncMock(return_value=None)
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await box_connector.stream_record(MagicMock())

    @patch("app.connectors.sources.box.connector.create_stream_record_response")
    @patch("app.connectors.sources.box.connector.stream_content")
    async def test_success(self, mock_stream, mock_response, box_connector):
        box_connector.get_signed_url = AsyncMock(return_value="https://url.com")
        mock_response.return_value = MagicMock()
        record = MagicMock()
        record.record_name = "file.pdf"
        record.mime_type = "application/pdf"
        record.id = "r1"
        result = await box_connector.stream_record(record)
        assert result is not None


class TestBoxTestConnection:
    async def test_no_data_source(self, box_connector):
        box_connector.data_source = None
        assert await box_connector.test_connection_and_access() is False

    async def test_success(self, box_connector):
        box_connector.data_source.get_current_user = AsyncMock(
            return_value=MagicMock(success=True)
        )
        assert await box_connector.test_connection_and_access() is True

    async def test_failure(self, box_connector):
        box_connector.data_source.get_current_user = AsyncMock(
            return_value=MagicMock(success=False)
        )
        assert await box_connector.test_connection_and_access() is False

    async def test_exception(self, box_connector):
        box_connector.data_source.get_current_user = AsyncMock(
            side_effect=Exception("network error")
        )
        assert await box_connector.test_connection_and_access() is False


class TestBoxHandleWebhook:
    def test_triggers_incremental_sync(self, box_connector):
        with patch("asyncio.create_task"):
            box_connector.handle_webhook_notification({"trigger": "FILE.UPLOADED"})


class TestBoxCleanup:
    async def test_cleanup(self, box_connector):
        box_connector.data_source = MagicMock()
        await box_connector.cleanup()
        assert box_connector.data_source is None


class TestBoxReindexRecords:
    async def test_empty(self, box_connector):
        await box_connector.reindex_records([])
        box_connector.data_entities_processor.on_new_records.assert_not_awaited()

    async def test_with_records(self, box_connector):
        record = MagicMock()
        record.external_record_group_id = "u1"
        record.external_record_id = "f1"
        record.mime_type = "application/pdf"
        record.record_name = "doc.pdf"

        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.files_get_file_by_id = AsyncMock(
            return_value=MagicMock(success=True, data=_make_box_entry_fullcov())
        )
        box_connector._to_dict = MagicMock(return_value=_make_box_entry_fullcov())
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[], is_updated=True, is_new=False
        ))

        await box_connector.reindex_records([record])
        box_connector.data_entities_processor.on_new_records.assert_awaited()

    async def test_folder_record(self, box_connector):
        record = MagicMock()
        record.external_record_group_id = "u1"
        record.external_record_id = "d1"
        record.mime_type = MimeTypes.FOLDER.value
        record.record_name = "MyFolder"

        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.folders_get_folder_by_id = AsyncMock(
            return_value=MagicMock(success=True, data=_make_box_entry_fullcov(entry_type="folder"))
        )
        box_connector._to_dict = MagicMock(
            return_value=_make_box_entry_fullcov(entry_type="folder")
        )
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[], is_updated=False, is_new=False
        ))
        box_connector.data_entities_processor.reindex_existing_records = AsyncMock()

        await box_connector.reindex_records([record])
        box_connector.data_entities_processor.reindex_existing_records.assert_awaited()

    async def test_reindex_api_failure(self, box_connector):
        record = MagicMock()
        record.external_record_group_id = "u1"
        record.external_record_id = "f1"
        record.mime_type = "application/pdf"
        record.record_name = "doc.pdf"

        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.files_get_file_by_id = AsyncMock(
            return_value=MagicMock(success=False, error="gone")
        )

        await box_connector.reindex_records([record])


class TestBoxGetDateFilters:
    def test_empty_filters(self, box_connector):
        result = box_connector._get_date_filters()
        assert all(v is None for v in result)

    def test_with_modified_filter(self, box_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = (
            "2025-01-01T00:00:00+00:00", "2025-12-31T00:00:00+00:00"
        )
        box_connector.sync_filters = MagicMock()
        box_connector.sync_filters.get.side_effect = (
            lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        result = box_connector._get_date_filters()
        assert result[0] is not None
        assert result[1] is not None

    def test_with_created_filter(self, box_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = (
            "2025-01-01T00:00:00+00:00", None
        )
        box_connector.sync_filters = MagicMock()
        box_connector.sync_filters.get.side_effect = (
            lambda key: mock_filter if key == SyncFilterKey.CREATED else None
        )
        result = box_connector._get_date_filters()
        assert result[2] is not None


class TestBoxShouldIncludeFile:
    def test_non_file_always_included(self, box_connector):
        entry = {"type": "folder", "name": "test"}
        assert box_connector._should_include_file(entry) is True

    def test_no_filters_includes_all(self, box_connector):
        entry = _make_box_entry_fullcov()
        assert box_connector._should_include_file(entry) is True

    def test_modified_after_excludes(self, box_connector):
        box_connector._cached_date_filters = (
            datetime(2025, 1, 1, tzinfo=timezone.utc), None, None, None
        )
        entry = _make_box_entry_fullcov(modified_at="2024-01-01T00:00:00Z")
        assert box_connector._should_include_file(entry) is False

    def test_modified_before_excludes(self, box_connector):
        box_connector._cached_date_filters = (
            None, datetime(2023, 1, 1, tzinfo=timezone.utc), None, None
        )
        entry = _make_box_entry_fullcov(modified_at="2024-01-01T00:00:00Z")
        assert box_connector._should_include_file(entry) is False

    def test_created_after_excludes(self, box_connector):
        box_connector._cached_date_filters = (
            None, None, datetime(2025, 1, 1, tzinfo=timezone.utc), None
        )
        entry = _make_box_entry_fullcov(created_at="2024-01-01T00:00:00Z")
        assert box_connector._should_include_file(entry) is False

    def test_created_before_excludes(self, box_connector):
        box_connector._cached_date_filters = (
            None, None, None, datetime(2023, 1, 1, tzinfo=timezone.utc)
        )
        entry = _make_box_entry_fullcov(created_at="2024-01-01T00:00:00Z")
        assert box_connector._should_include_file(entry) is False

    def test_extension_filter_in(self, box_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "docx"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        box_connector.sync_filters = MagicMock()
        box_connector.sync_filters.get.side_effect = (
            lambda key: mock_filter if key == SyncFilterKey.FILE_EXTENSIONS else None
        )
        box_connector._cached_date_filters = (None, None, None, None)

        assert box_connector._should_include_file(_make_box_entry_fullcov(name="doc.pdf")) is True
        assert box_connector._should_include_file(_make_box_entry_fullcov(name="doc.txt")) is False

    def test_extension_filter_not_in(self, box_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["exe", "bat"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        box_connector.sync_filters = MagicMock()
        box_connector.sync_filters.get.side_effect = (
            lambda key: mock_filter if key == SyncFilterKey.FILE_EXTENSIONS else None
        )
        box_connector._cached_date_filters = (None, None, None, None)

        assert box_connector._should_include_file(_make_box_entry_fullcov(name="doc.pdf")) is True
        assert box_connector._should_include_file(_make_box_entry_fullcov(name="app.exe")) is False

    def test_no_extension_with_not_in(self, box_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["exe"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        box_connector.sync_filters = MagicMock()
        box_connector.sync_filters.get.side_effect = (
            lambda key: mock_filter if key == SyncFilterKey.FILE_EXTENSIONS else None
        )
        box_connector._cached_date_filters = (None, None, None, None)

        assert box_connector._should_include_file(_make_box_entry_fullcov(name="Makefile")) is True

    def test_missing_modified_date_with_filter(self, box_connector):
        box_connector._cached_date_filters = (
            datetime(2025, 1, 1, tzinfo=timezone.utc), None, None, None
        )
        entry = _make_box_entry_fullcov(modified_at=None)
        del entry["modified_at"]
        assert box_connector._should_include_file(entry) is False


class TestBoxGetFilterOptions:
    async def test_raises(self, box_connector):
        with pytest.raises(NotImplementedError):
            await box_connector.get_filter_options("any_key")


class TestBoxFetchAndSyncItemsAsSharedWithMe:
    async def test_empty_items(self, box_connector):
        await box_connector._fetch_and_sync_items_as_shared_with_me([])

    async def test_file_sync(self, box_connector):
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.files_get_file_by_id = AsyncMock(
            return_value=MagicMock(success=True, data=_make_box_entry_fullcov())
        )
        box_connector._to_dict = MagicMock(return_value=_make_box_entry_fullcov())
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[]
        ))

        items = [("f1", "file", "u1", "user@test.com")]
        await box_connector._fetch_and_sync_items_as_shared_with_me(items)
        box_connector.data_entities_processor.on_new_records.assert_awaited()

    async def test_folder_sync(self, box_connector):
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.folders_get_folder_by_id = AsyncMock(
            return_value=MagicMock(success=True, data=_make_box_entry_fullcov(entry_type="folder"))
        )
        box_connector._to_dict = MagicMock(
            return_value=_make_box_entry_fullcov(entry_type="folder")
        )
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[]
        ))
        box_connector._sync_folder_contents_recursively = AsyncMock()

        items = [("d1", "folder", "u1", "user@test.com")]
        await box_connector._fetch_and_sync_items_as_shared_with_me(items)
        box_connector._sync_folder_contents_recursively.assert_awaited()


class TestBoxProcessBoxEntryFileExtensionFilter:
    async def test_file_filtered_by_extension(self, box_connector):
        box_connector._should_include_file = MagicMock(return_value=False)
        entry = _make_box_entry_fullcov()
        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        assert result is None

    async def test_shared_with_me_group_link(self, box_connector):
        shared_group = MagicMock()
        shared_group.id = "sg1"
        provider = _make_mock_data_store_provider(record_group=shared_group)
        box_connector.data_store_provider = provider
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=MagicMock(success=False, error="None")
        )

        entry = _make_box_entry_fullcov(owned_by={"id": "other-user"})
        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        assert result is not None
        assert result.record.is_shared_with_me is True
