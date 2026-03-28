"""Extended coverage tests for the Box connector."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.box.connector import (
    BoxConnector,
    get_file_extension,
    get_mimetype_enum_for_box,
    get_parent_path_from_path,
)
from app.models.entities import AppUser, AppUserGroup, RecordGroup, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_tx_store(existing_record=None, record_group=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_record_group_by_external_id = AsyncMock(return_value=record_group)
    tx.create_record_group_relation = AsyncMock()
    tx.get_app_user_by_email = AsyncMock(return_value=None)
    tx.get_user_groups = AsyncMock(return_value=[])
    tx.get_records_by_parent = AsyncMock(return_value=[])
    tx.remove_user_access_to_record = AsyncMock()
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


def _make_connector():
    logger = logging.getLogger("test.box")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-box-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.on_user_group_deleted = AsyncMock()
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.get_all_app_users = AsyncMock(return_value=[])

    data_store_provider = _make_mock_data_store_provider()
    config_service = AsyncMock()
    connector_id = "box-test-1"

    connector = BoxConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id=connector_id,
    )
    return connector


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


def _make_box_response(success=True, data=None, error=None):
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.error = error
    return resp


# ---------------------------------------------------------------------------
# Tests: Module-level helpers
# ---------------------------------------------------------------------------

class TestGetParentPathFromPath:
    def test_normal_path(self):
        assert get_parent_path_from_path("/All Files/Folder/file.txt") == "/All Files/Folder"

    def test_root_file(self):
        assert get_parent_path_from_path("/file.txt") is None

    def test_slash_only(self):
        assert get_parent_path_from_path("/") is None

    def test_empty_string(self):
        assert get_parent_path_from_path("") is None

    def test_none(self):
        assert get_parent_path_from_path(None) is None

    def test_deep_path(self):
        assert get_parent_path_from_path("/a/b/c/d.txt") == "/a/b/c"

    def test_single_folder(self):
        assert get_parent_path_from_path("/Folder/sub") == "/Folder"


class TestGetFileExtension:
    def test_normal_extension(self):
        assert get_file_extension("document.pdf") == "pdf"

    def test_double_extension(self):
        assert get_file_extension("archive.tar.gz") == "gz"

    def test_no_extension(self):
        assert get_file_extension("README") is None

    def test_hidden_file(self):
        assert get_file_extension(".gitignore") == "gitignore"

    def test_uppercase_extension(self):
        assert get_file_extension("PHOTO.JPG") == "jpg"


class TestGetMimetypeEnumForBox:
    def test_folder(self):
        result = get_mimetype_enum_for_box("folder")
        assert result == MimeTypes.FOLDER

    def test_known_file_type(self):
        result = get_mimetype_enum_for_box("file", "document.pdf")
        assert result == MimeTypes.PDF

    def test_unknown_file_type(self):
        result = get_mimetype_enum_for_box("file", "data.xyz123")
        assert result == MimeTypes.BIN

    def test_file_no_name(self):
        result = get_mimetype_enum_for_box("file", None)
        assert result == MimeTypes.BIN

    def test_file_empty_name(self):
        result = get_mimetype_enum_for_box("file", "")
        assert result == MimeTypes.BIN


# ---------------------------------------------------------------------------
# Tests: _parse_box_timestamp
# ---------------------------------------------------------------------------

class TestParseBoxTimestamp:
    def test_valid_timestamp(self):
        connector = _make_connector()
        result = connector._parse_box_timestamp("2024-01-15T10:30:00Z", "created_at", "file.txt")
        assert result > 0

    def test_none_timestamp(self):
        connector = _make_connector()
        result = connector._parse_box_timestamp(None, "created_at", "file.txt")
        assert result > 0  # Falls back to current time

    def test_invalid_timestamp(self):
        connector = _make_connector()
        result = connector._parse_box_timestamp("not-a-date", "created_at", "file.txt")
        assert result > 0  # Falls back to current time

    def test_iso_format_with_timezone(self):
        connector = _make_connector()
        result = connector._parse_box_timestamp("2024-01-15T10:30:00+00:00", "created_at", "file.txt")
        assert result > 0


# ---------------------------------------------------------------------------
# Tests: _to_dict
# ---------------------------------------------------------------------------

class TestToDict:
    def test_dict_passthrough(self):
        connector = _make_connector()
        d = {"key": "value"}
        assert connector._to_dict(d) == d

    def test_none_returns_empty(self):
        connector = _make_connector()
        assert connector._to_dict(None) == {}

    def test_object_with_to_dict(self):
        connector = _make_connector()
        obj = MagicMock()
        obj.to_dict.return_value = {"a": 1}
        assert connector._to_dict(obj) == {"a": 1}

    def test_object_with_response_object(self):
        connector = _make_connector()
        obj = MagicMock(spec=[])
        obj.response_object = {"b": 2}
        assert connector._to_dict(obj) == {"b": 2}

    def test_fallback_to_empty(self):
        connector = _make_connector()
        # An object with no known conversion methods
        obj = 42
        assert connector._to_dict(obj) == {}


# ---------------------------------------------------------------------------
# Tests: _get_permissions
# ---------------------------------------------------------------------------

class TestGetPermissions:
    @pytest.mark.asyncio
    async def test_file_permissions(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=_make_box_response(
                success=True,
                data={
                    "entries": [
                        {
                            "accessible_by": {"id": "u1", "type": "user", "login": "user@test.com"},
                            "role": "editor",
                        }
                    ]
                },
            )
        )
        perms = await connector._get_permissions("f1", "file")
        assert len(perms) == 1
        assert perms[0].type == PermissionType.WRITE

    @pytest.mark.asyncio
    async def test_folder_permissions(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.collaborations_get_folder_collaborations = AsyncMock(
            return_value=_make_box_response(
                success=True,
                data={
                    "entries": [
                        {
                            "accessible_by": {"id": "u1", "type": "user", "login": "user@test.com"},
                            "role": "owner",
                        }
                    ]
                },
            )
        )
        perms = await connector._get_permissions("d1", "folder")
        assert len(perms) == 1
        assert perms[0].type == PermissionType.OWNER

    @pytest.mark.asyncio
    async def test_group_entity_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=_make_box_response(
                success=True,
                data={
                    "entries": [
                        {
                            "accessible_by": {"id": "g1", "type": "group", "login": ""},
                            "role": "viewer",
                        }
                    ]
                },
            )
        )
        perms = await connector._get_permissions("f1", "file")
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_failed_response(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=_make_box_response(success=False, error="404 Not Found")
        )
        perms = await connector._get_permissions("f1", "file")
        assert perms == []

    @pytest.mark.asyncio
    async def test_skip_no_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=_make_box_response(
                success=True,
                data={
                    "entries": [
                        {
                            "accessible_by": {"id": None, "type": "user"},
                            "role": "viewer",
                        }
                    ]
                },
            )
        )
        perms = await connector._get_permissions("f1", "file")
        assert perms == []

    @pytest.mark.asyncio
    async def test_co_owner_maps_to_write(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=_make_box_response(
                success=True,
                data={
                    "entries": [
                        {
                            "accessible_by": {"id": "u1", "type": "user", "login": "a@test.com"},
                            "role": "co-owner",
                        }
                    ]
                },
            )
        )
        perms = await connector._get_permissions("f1", "file")
        assert perms[0].type == PermissionType.WRITE


# ---------------------------------------------------------------------------
# Tests: _process_box_entry
# ---------------------------------------------------------------------------

class TestProcessBoxEntry:
    @pytest.mark.asyncio
    async def test_new_file_entry(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=_make_box_response(success=True, data={"entries": []})
        )
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        entry = _make_box_entry()
        result = await connector._process_box_entry(entry, "user1", "user@test.com", "rg-1")
        assert result is not None
        assert result.is_new is True
        assert result.record.record_type == RecordType.FILE

    @pytest.mark.asyncio
    async def test_folder_entry(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.collaborations_get_folder_collaborations = AsyncMock(
            return_value=_make_box_response(success=True, data={"entries": []})
        )
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        entry = _make_box_entry(entry_type="folder", name="MyFolder")
        result = await connector._process_box_entry(entry, "user1", "user@test.com", "rg-1")
        assert result is not None
        assert result.record.mime_type == MimeTypes.FOLDER.value

    @pytest.mark.asyncio
    async def test_entry_no_id_returns_none(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        entry = {"type": "file", "name": "test.pdf"}
        result = await connector._process_box_entry(entry, "user1", "user@test.com", "rg-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_entry_no_name_returns_none(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        entry = {"type": "file", "id": "f1"}
        result = await connector._process_box_entry(entry, "user1", "user@test.com", "rg-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_shared_link_company(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=_make_box_response(success=True, data={"entries": []})
        )
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        entry = _make_box_entry(shared_link={"access": "company"})
        result = await connector._process_box_entry(entry, "user1", "user@test.com", "rg-1")
        assert result is not None
        # Check that org-level permission was added
        has_group_perm = any(p.entity_type == EntityType.GROUP for p in result.new_permissions)
        assert has_group_perm

    @pytest.mark.asyncio
    async def test_shared_link_open(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=_make_box_response(success=True, data={"entries": []})
        )
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        entry = _make_box_entry(shared_link={"access": "open"})
        result = await connector._process_box_entry(entry, "user1", "user@test.com", "rg-1")
        assert result is not None
        has_public_perm = any(
            p.external_id == "PUBLIC" for p in result.new_permissions
        )
        assert has_public_perm

    @pytest.mark.asyncio
    async def test_existing_record_marks_updated(self):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.source_updated_at = 1000
        connector = _make_connector()
        connector.data_store_provider = _make_mock_data_store_provider(existing)
        connector.data_source = MagicMock()
        connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=_make_box_response(success=True, data={"entries": []})
        )
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        entry = _make_box_entry()
        result = await connector._process_box_entry(entry, "user1", "user@test.com", "rg-1")
        assert result is not None
        assert result.is_new is False
        assert result.is_updated is True

    @pytest.mark.asyncio
    async def test_shared_with_me_detection(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=_make_box_response(success=True, data={"entries": []})
        )
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        entry = _make_box_entry(owned_by={"id": "other-user"})
        result = await connector._process_box_entry(entry, "user1", "user@test.com", "rg-1")
        assert result is not None
        assert result.record.is_shared_with_me is True


# ---------------------------------------------------------------------------
# Tests: _sync_users
# ---------------------------------------------------------------------------

class TestSyncUsers:
    @pytest.mark.asyncio
    async def test_sync_users_basic(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.users_get_users = AsyncMock(
            return_value=_make_box_response(
                success=True,
                data={
                    "entries": [
                        {"id": "u1", "login": "user1@test.com", "name": "User One", "status": "active", "job_title": "Dev"},
                        {"id": "u2", "login": "user2@test.com", "name": "User Two", "status": "active"},
                    ]
                },
            )
        )
        users = await connector._sync_users()
        assert len(users) == 2
        assert users[0].email == "user1@test.com"

    @pytest.mark.asyncio
    async def test_sync_users_failed_response(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.users_get_users = AsyncMock(
            return_value=_make_box_response(success=False, error="Error")
        )
        users = await connector._sync_users()
        assert users == []

    @pytest.mark.asyncio
    async def test_sync_users_empty(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.users_get_users = AsyncMock(
            return_value=_make_box_response(success=True, data={"entries": []})
        )
        users = await connector._sync_users()
        assert users == []


# ---------------------------------------------------------------------------
# Tests: _ensure_virtual_groups
# ---------------------------------------------------------------------------

class TestEnsureVirtualGroups:
    @pytest.mark.asyncio
    async def test_creates_virtual_groups(self):
        connector = _make_connector()
        await connector._ensure_virtual_groups()
        connector.data_entities_processor.on_new_user_groups.assert_called_once()
        args = connector.data_entities_processor.on_new_user_groups.call_args[0][0]
        assert len(args) == 2
        group_ids = [g[0].source_user_group_id for g in args]
        assert "PUBLIC" in group_ids


# ---------------------------------------------------------------------------
# Tests: _handle_record_updates
# ---------------------------------------------------------------------------

class TestHandleRecordUpdates:
    @pytest.mark.asyncio
    async def test_deleted_record(self):
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "rec-1"

        update = MagicMock()
        update.is_deleted = True
        update.is_updated = False
        update.external_record_id = "ext-1"

        connector.data_store_provider = _make_mock_data_store_provider(existing)
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_called_once()

    @pytest.mark.asyncio
    async def test_updated_record(self):
        connector = _make_connector()
        update = MagicMock()
        update.is_deleted = False
        update.is_updated = True
        update.record = MagicMock()
        update.new_permissions = []

        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_new_records.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: _reconcile_deleted_groups
# ---------------------------------------------------------------------------

class TestReconcileDeletedGroups:
    @pytest.mark.asyncio
    async def test_no_stale_groups(self):
        connector = _make_connector()
        mock_group = MagicMock()
        mock_group.source_user_group_id = "g1"
        tx = _make_mock_tx_store()
        tx.get_user_groups = AsyncMock(return_value=[mock_group])
        connector.data_store_provider = _make_mock_data_store_provider()

        # Need to set up the provider to return the right tx store
        @asynccontextmanager
        async def _transaction():
            yield tx
        connector.data_store_provider.transaction = _transaction

        await connector._reconcile_deleted_groups({"g1"})
        connector.data_entities_processor.on_user_group_deleted.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_groups_deleted(self):
        connector = _make_connector()
        mock_group = MagicMock()
        mock_group.source_user_group_id = "g-stale"
        mock_group.name = "Stale Group"
        tx = _make_mock_tx_store()
        tx.get_user_groups = AsyncMock(return_value=[mock_group])

        @asynccontextmanager
        async def _transaction():
            yield tx
        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        await connector._reconcile_deleted_groups({"g-active"})
        connector.data_entities_processor.on_user_group_deleted.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: _get_app_users_by_emails
# ---------------------------------------------------------------------------

class TestGetAppUsersByEmails:
    @pytest.mark.asyncio
    async def test_empty_emails(self):
        connector = _make_connector()
        result = await connector._get_app_users_by_emails([])
        assert result == []

    @pytest.mark.asyncio
    async def test_found_users(self):
        connector = _make_connector()
        mock_user = MagicMock()
        tx = _make_mock_tx_store()
        tx.get_app_user_by_email = AsyncMock(return_value=mock_user)

        @asynccontextmanager
        async def _transaction():
            yield tx
        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        result = await connector._get_app_users_by_emails(["user@test.com"])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tests: _should_include_file / _get_date_filters
# ---------------------------------------------------------------------------

class TestShouldIncludeFile:
    def test_no_filters_includes_all(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector._cached_date_filters = (None, None, None, None)
        entry = _make_box_entry()
        assert connector._should_include_file(entry) is True


class TestGetDateFilters:
    def test_no_filters_returns_none_tuple(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        result = connector._get_date_filters()
        assert result == (None, None, None, None)


# ---------------------------------------------------------------------------
# Tests: init
# ---------------------------------------------------------------------------

class TestInit:
    @pytest.mark.asyncio
    async def test_no_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_no_auth_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={"some": "data"})
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_credentials(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"clientId": "id", "clientSecret": None, "enterpriseId": "eid"}
        })
        result = await connector.init()
        assert result is False


# ---------------------------------------------------------------------------
# Tests: _process_box_items_generator
# ---------------------------------------------------------------------------

class TestProcessBoxItemsGenerator:
    @pytest.mark.asyncio
    async def test_yields_new_records(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=_make_box_response(success=True, data={"entries": []})
        )
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        entries = [_make_box_entry(entry_id="f1"), _make_box_entry(entry_id="f2", name="file2.txt")]
        results = []
        async for file_record, permissions, update in connector._process_box_items_generator(
            entries, "user1", "user@test.com", "rg-1"
        ):
            results.append((file_record, permissions, update))
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Tests: _remove_user_access_from_folder_recursively
# ---------------------------------------------------------------------------

class TestRemoveUserAccessRecursively:
    @pytest.mark.asyncio
    async def test_removes_access(self):
        connector = _make_connector()
        tx = _make_mock_tx_store()
        tx.get_records_by_parent = AsyncMock(return_value=[])

        @asynccontextmanager
        async def _transaction():
            yield tx
        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        await connector._remove_user_access_from_folder_recursively("folder-1", "user-1")
        tx.remove_user_access_to_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_children(self):
        connector = _make_connector()
        child = MagicMock()
        child.external_record_id = "child-1"
        tx = _make_mock_tx_store()
        # First call returns children, second returns empty
        tx.get_records_by_parent = AsyncMock(side_effect=[[child], []])

        @asynccontextmanager
        async def _transaction():
            yield tx
        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        await connector._remove_user_access_from_folder_recursively("folder-1", "user-1")
        assert tx.remove_user_access_to_record.call_count == 2
