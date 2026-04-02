"""Tests for GoogleDriveTeamConnector (app/connectors/sources/google/drive/team/connector.py)."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import FilterCollection
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger():
    log = logging.getLogger("test_drive_team")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
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


def _make_google_user(email="user@example.com", user_id="guser-1", full_name="Test User"):
    return {
        "id": user_id,
        "primaryEmail": email,
        "name": {"fullName": full_name, "givenName": "Test", "familyName": "User"},
        "suspended": False,
        "creationTime": "2024-01-01T00:00:00.000Z",
        "organizations": [{"title": "Engineer"}],
    }


def _make_google_group(email="group@example.com", group_id=None, name="Engineering"):
    return {
        "id": group_id or f"grp-{email}",
        "email": email,
        "name": name,
        "description": "Test group",
        "creationTime": "2024-01-01T00:00:00.000Z",
    }


def _make_file_metadata(
    file_id="file-1",
    name="test.txt",
    mime_type="text/plain",
    created_time="2025-01-01T00:00:00Z",
    modified_time="2025-01-15T00:00:00Z",
    parents=None,
    shared=False,
    head_revision_id="rev-1",
    file_extension="txt",
    size=1024,
    owners=None,
):
    meta = {
        "id": file_id,
        "name": name,
        "mimeType": mime_type,
        "createdTime": created_time,
        "modifiedTime": modified_time,
        "shared": shared,
        "headRevisionId": head_revision_id,
        "fileExtension": file_extension,
        "size": size,
        "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
    }
    if parents is not None:
        meta["parents"] = parents
    if owners is not None:
        meta["owners"] = owners
    return meta


def _make_shared_drive(drive_id="shared-drive-1", name="Shared Drive"):
    return {
        "id": drive_id,
        "name": name,
        "createdTime": "2024-06-01T00:00:00.000Z",
    }


@pytest.fixture
def connector():
    """Create a GoogleDriveTeamConnector with fully mocked dependencies."""
    with patch(
        "app.connectors.sources.google.drive.team.connector.GoogleClient"
    ), patch(
        "app.connectors.sources.google.drive.team.connector.SyncPoint"
    ) as MockSyncPoint:
        mock_sync_point = AsyncMock()
        mock_sync_point.read_sync_point = AsyncMock(return_value=None)
        mock_sync_point.update_sync_point = AsyncMock()
        MockSyncPoint.return_value = mock_sync_point

        from app.connectors.sources.google.drive.team.connector import (
            GoogleDriveTeamConnector,
        )

        logger = _make_logger()
        dep = AsyncMock()
        dep.org_id = "org-123"
        dep.on_new_records = AsyncMock()
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_new_user_groups = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.on_updated_record_permissions = AsyncMock()
        dep.add_permission_to_record = AsyncMock()

        ds_provider = _make_mock_data_store_provider()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"adminEmail": "admin@example.com"},
            "credentials": {},
        })

        conn = GoogleDriveTeamConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="drive-team-1",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.admin_client = MagicMock()
        conn.drive_client = MagicMock()
        conn.admin_data_source = AsyncMock()
        conn.drive_data_source = AsyncMock()
        conn.config = {"credentials": {}}
        yield conn


# ---------------------------------------------------------------------------
# _sync_users
# ---------------------------------------------------------------------------

class TestSyncUsers:
    """Tests for enterprise user sync."""

    async def test_syncs_users_from_admin_api(self, connector):
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [
                _make_google_user("alice@example.com", "u1", "Alice"),
                _make_google_user("bob@example.com", "u2", "Bob"),
            ],
        })

        await connector._sync_users()

        connector.data_entities_processor.on_new_app_users.assert_called_once()
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(users) == 2
        assert users[0].email == "alice@example.com"
        assert users[1].email == "bob@example.com"
        assert len(connector.synced_users) == 2

    async def test_sync_users_handles_pagination(self, connector):
        connector.admin_data_source.users_list = AsyncMock(side_effect=[
            {
                "users": [_make_google_user("alice@example.com", "u1", "Alice")],
                "nextPageToken": "page2",
            },
            {
                "users": [_make_google_user("bob@example.com", "u2", "Bob")],
            },
        ])

        await connector._sync_users()

        assert connector.admin_data_source.users_list.call_count == 2
        assert len(connector.synced_users) == 2

    async def test_sync_users_skips_user_without_email(self, connector):
        user_no_email = _make_google_user()
        user_no_email["primaryEmail"] = None
        user_no_email["email"] = ""

        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [user_no_email],
        })

        await connector._sync_users()
        assert len(connector.synced_users) == 0

    async def test_sync_users_handles_empty_response(self, connector):
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": []})

        await connector._sync_users()
        assert len(connector.synced_users) == 0

    async def test_sync_users_raises_when_no_admin_source(self, connector):
        connector.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector._sync_users()

    async def test_user_active_status_mapped(self, connector):
        suspended_user = _make_google_user()
        suspended_user["suspended"] = True

        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [suspended_user],
        })

        await connector._sync_users()
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert users[0].is_active is False


# ---------------------------------------------------------------------------
# _sync_user_groups
# ---------------------------------------------------------------------------

class TestSyncUserGroups:
    """Tests for enterprise group sync."""

    async def test_syncs_groups_and_members(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [_make_google_group("eng@example.com")],
        })
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [
                {"type": "USER", "id": "u1", "email": "alice@example.com"},
            ],
        })

        await connector._sync_user_groups()

        connector.data_entities_processor.on_new_user_groups.assert_called_once()

    async def test_skips_non_user_members(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [_make_google_group("eng@example.com")],
        })
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [
                {"type": "GROUP", "id": "g1", "email": "subgroup@example.com"},
                {"type": "CUSTOMER", "id": "c1", "email": "customer@example.com"},
            ],
        })

        await connector._sync_user_groups()
        # Group with no user members should not call on_new_user_groups
        connector.data_entities_processor.on_new_user_groups.assert_not_called()


# ---------------------------------------------------------------------------
# Permission mapping
# ---------------------------------------------------------------------------

class TestPermissionMapping:
    """Tests for Drive role → PermissionType mapping."""

    def test_owner_role(self, connector):
        assert connector._map_drive_role_to_permission_type("owner") == PermissionType.OWNER

    def test_organizer_role(self, connector):
        assert connector._map_drive_role_to_permission_type("organizer") == PermissionType.OWNER

    def test_writer_role(self, connector):
        assert connector._map_drive_role_to_permission_type("writer") == PermissionType.WRITE

    def test_fileorganizer_role(self, connector):
        assert connector._map_drive_role_to_permission_type("fileOrganizer") == PermissionType.WRITE

    def test_commenter_role(self, connector):
        assert connector._map_drive_role_to_permission_type("commenter") == PermissionType.COMMENT

    def test_reader_role(self, connector):
        assert connector._map_drive_role_to_permission_type("reader") == PermissionType.READ

    def test_unknown_role_defaults_to_read(self, connector):
        assert connector._map_drive_role_to_permission_type("unknown") == PermissionType.READ


class TestEntityTypeMapping:
    """Tests for Drive permission type → EntityType mapping."""

    def test_user_type(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("user") == EntityType.USER

    def test_group_type(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("group") == EntityType.GROUP

    def test_domain_type(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("domain") == EntityType.DOMAIN

    def test_anyone_type(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("anyone") == EntityType.ANYONE

    def test_anyone_with_link_type(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("anyoneWithLink") == EntityType.ANYONE_WITH_LINK

    def test_unknown_type_defaults_to_user(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("unknown") == EntityType.USER


# ---------------------------------------------------------------------------
# _fetch_permissions
# ---------------------------------------------------------------------------

class TestFetchPermissions:
    """Tests for _fetch_permissions."""

    async def test_fetches_file_permissions(self, connector):
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "perm1", "role": "owner", "type": "user", "emailAddress": "owner@example.com"},
                {"id": "perm2", "role": "reader", "type": "user", "emailAddress": "reader@example.com"},
            ],
        })

        perms, is_fallback = await connector._fetch_permissions("file-1", is_drive=False)
        assert len(perms) == 2
        assert perms[0].type == PermissionType.OWNER
        assert perms[1].type == PermissionType.READ
        assert is_fallback is False

    async def test_skips_deleted_permissions(self, connector):
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "perm1", "role": "owner", "type": "user", "emailAddress": "owner@example.com", "deleted": True},
            ],
        })

        perms, _ = await connector._fetch_permissions("file-1", is_drive=False)
        assert len(perms) == 0

    async def test_403_creates_fallback_permission_for_file(self, connector):
        from googleapiclient.errors import HttpError

        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "insufficientFilePermissions"}]

        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        perms, is_fallback = await connector._fetch_permissions(
            "file-1", is_drive=False, user_email="user@example.com"
        )
        assert is_fallback is True
        assert len(perms) == 1
        assert perms[0].email == "user@example.com"
        assert perms[0].type == PermissionType.READ


# ---------------------------------------------------------------------------
# _sync_record_groups
# ---------------------------------------------------------------------------

class TestSyncRecordGroups:
    """Tests for syncing shared drives and personal drives."""

    async def test_syncs_shared_drives(self, connector):
        connector.drive_data_source.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "organizer", "type": "user", "emailAddress": "admin@example.com"},
            ],
        })
        connector.synced_users = []  # No personal drives

        await connector._sync_record_groups()

        # Should create record group for the shared drive
        connector.data_entities_processor.on_new_record_groups.assert_called()

    async def test_raises_when_drive_source_not_initialized(self, connector):
        connector.drive_data_source = None
        with pytest.raises(ValueError, match="Drive data source not initialized"):
            await connector._sync_record_groups()


# ---------------------------------------------------------------------------
# _process_drive_item (team)
# ---------------------------------------------------------------------------

class TestTeamProcessDriveItem:
    """Tests for the team connector's _process_drive_item with shared drive awareness."""

    async def test_new_file_from_shared_drive(self, connector):
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "writer", "type": "user", "emailAddress": "user@example.com"},
            ],
        })

        metadata = _make_file_metadata(parents=["sd-1"])
        result = await connector._process_drive_item(
            metadata=metadata,
            user_id="u1",
            user_email="user@example.com",
            drive_id="sd-1",
            is_shared_drive=True,
        )

        assert result is not None
        assert result.is_new is True
        assert result.record.external_record_group_id == "sd-1"

    async def test_shared_with_me_detection(self, connector):
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "user", "emailAddress": "reader@example.com"},
            ],
        })

        metadata = _make_file_metadata(
            shared=True,
            owners=[{"emailAddress": "owner@example.com"}],
            parents=["parent-folder"],
        )
        result = await connector._process_drive_item(
            metadata=metadata,
            user_id="u1",
            user_email="reader@example.com",
            drive_id="drive-1",
            is_shared_drive=False,
        )

        assert result is not None
        assert result.record.is_shared_with_me is True
        # Shared-with-me files get external_record_group_id set to None
        assert result.record.external_record_group_id is None


# ---------------------------------------------------------------------------
# _handle_drive_error
# ---------------------------------------------------------------------------

class TestHandleDriveError:
    """Tests for _handle_drive_error."""

    async def test_403_team_drive_membership_required(self, connector):
        from googleapiclient.errors import HttpError

        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "teamDriveMembershipRequired"}]

        should_continue = await connector._handle_drive_error(
            http_error, "Test Drive", "drive-1", "files"
        )
        assert should_continue is True

    async def test_generic_error_returns_true(self, connector):
        error = Exception("generic error")
        should_continue = await connector._handle_drive_error(
            error, "Test Drive", "drive-1", "files"
        )
        assert should_continue is True


# ---------------------------------------------------------------------------
# run_sync orchestration
# ---------------------------------------------------------------------------

class TestTeamRunSync:
    """Tests for the team connector's main run_sync orchestration."""

    async def test_run_sync_calls_all_steps(self, connector):
        with patch.object(connector, "_sync_users", new_callable=AsyncMock) as mock_users, \
             patch.object(connector, "_sync_user_groups", new_callable=AsyncMock) as mock_groups, \
             patch.object(connector, "_sync_record_groups", new_callable=AsyncMock) as mock_rg, \
             patch.object(connector, "_process_users_in_batches", new_callable=AsyncMock) as mock_batches, \
             patch(
                 "app.connectors.sources.google.drive.team.connector.load_connector_filters",
                 new_callable=AsyncMock,
                 return_value=(FilterCollection(), FilterCollection()),
             ):
            await connector.run_sync()

            mock_users.assert_called_once()
            mock_groups.assert_called_once()
            mock_rg.assert_called_once()
            mock_batches.assert_called_once()

    async def test_run_sync_propagates_errors(self, connector):
        with patch.object(
            connector, "_sync_users", new_callable=AsyncMock, side_effect=ValueError("api error")
        ), patch(
            "app.connectors.sources.google.drive.team.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            with pytest.raises(ValueError, match="api error"):
                await connector.run_sync()


# ---------------------------------------------------------------------------
# Date and extension filters (team connector has same logic)
# ---------------------------------------------------------------------------

class TestTeamDateFilters:
    """Tests for _pass_date_filters in team connector."""

    def test_folders_always_pass(self, connector):
        metadata = {
            "mimeType": MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            "createdTime": "2020-01-01T00:00:00Z",
        }
        assert connector._pass_date_filters(metadata) is True

    def test_no_filters_passes(self, connector):
        metadata = {"mimeType": "text/plain", "createdTime": "2025-01-01T00:00:00Z"}
        assert connector._pass_date_filters(metadata) is True


class TestTeamExtensionFilters:
    """Tests for _pass_extension_filter in team connector."""

    def test_folders_always_pass(self, connector):
        metadata = {"mimeType": MimeTypes.GOOGLE_DRIVE_FOLDER.value}
        assert connector._pass_extension_filter(metadata) is True

    def test_no_filter_passes(self, connector):
        metadata = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert connector._pass_extension_filter(metadata) is True


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

class TestTeamInit:
    """Tests for the team connector's init method."""

    async def test_init_returns_false_when_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    async def test_init_raises_when_no_auth(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        with pytest.raises(ValueError, match="Service account credentials not found"):
            await connector.init()

    async def test_init_raises_when_no_admin_email(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"someKey": "someValue"},
        })
        with pytest.raises(ValueError, match="Admin email not found"):
            await connector.init()
