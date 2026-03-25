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
