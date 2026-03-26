"""Deep sync loop tests for GoogleDriveTeamConnector."""

import asyncio
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import FilterCollection
from app.models.entities import (
    AppUser,
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
    log = logging.getLogger("test_drive_team_deep_sync")
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
    version=None,
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
    if version is not None:
        meta["version"] = version
    return meta


def _make_app_user(email="user@example.com", user_id="u1", full_name="Test User"):
    return AppUser(
        app_name=Connectors.GOOGLE_DRIVE_WORKSPACE,
        connector_id="drive-team-1",
        source_user_id=user_id,
        email=email,
        full_name=full_name,
        is_active=True,
    )


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
        dep.delete_permission_from_record = AsyncMock()
        dep.get_all_active_users = AsyncMock(return_value=[])

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
# _sync_users deep paths
# ---------------------------------------------------------------------------

class TestSyncUsersDeep:
    """Deep tests for _sync_users pagination and edge cases."""

    async def test_user_with_only_given_family_name(self, connector):
        """User without fullName, but with givenName + familyName."""
        user = {
            "id": "u1",
            "primaryEmail": "test@example.com",
            "name": {"givenName": "John", "familyName": "Doe"},
            "suspended": False,
        }
        connector.admin_data_source.users_list = AsyncMock(
            return_value={"users": [user]}
        )
        await connector._sync_users()
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert users[0].full_name == "John Doe"

    async def test_user_with_no_name_falls_back_to_email(self, connector):
        """User without any name info."""
        user = {
            "id": "u2",
            "primaryEmail": "noname@example.com",
            "name": {},
            "suspended": False,
        }
        connector.admin_data_source.users_list = AsyncMock(
            return_value={"users": [user]}
        )
        await connector._sync_users()
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert users[0].full_name == "noname@example.com"

    async def test_user_title_from_organizations(self, connector):
        """Title extracted from organizations array."""
        user = {
            "id": "u3",
            "primaryEmail": "titled@example.com",
            "name": {"fullName": "Title User"},
            "organizations": [{"title": "Senior Engineer"}],
            "suspended": False,
        }
        connector.admin_data_source.users_list = AsyncMock(
            return_value={"users": [user]}
        )
        await connector._sync_users()
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert users[0].title == "Senior Engineer"

    async def test_user_with_bad_creation_time(self, connector):
        """User with unparseable creationTime still syncs."""
        user = {
            "id": "u4",
            "primaryEmail": "badtime@example.com",
            "name": {"fullName": "Bad Time"},
            "creationTime": "not-a-date",
            "suspended": False,
        }
        connector.admin_data_source.users_list = AsyncMock(
            return_value={"users": [user]}
        )
        await connector._sync_users()
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert users[0].email == "badtime@example.com"
        assert users[0].source_created_at is None

    async def test_user_processing_error_continues(self, connector):
        """One user fails processing but others continue."""
        good_user = {
            "id": "u5",
            "primaryEmail": "good@example.com",
            "name": {"fullName": "Good User"},
            "suspended": False,
        }
        # This user will cause issues in parse_timestamp
        bad_user = {
            "id": "u6",
            "primaryEmail": None,
            "email": "",
            "name": {"fullName": "Bad User"},
            "suspended": False,
        }
        connector.admin_data_source.users_list = AsyncMock(
            return_value={"users": [bad_user, good_user]}
        )
        await connector._sync_users()
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(users) == 1
        assert users[0].email == "good@example.com"

    async def test_pagination_error_raises(self, connector):
        """Error during pagination raises exception."""
        connector.admin_data_source.users_list = AsyncMock(
            side_effect=RuntimeError("API error")
        )
        with pytest.raises(RuntimeError, match="API error"):
            await connector._sync_users()

    async def test_three_page_pagination(self, connector):
        """Three pages of users are fetched correctly."""
        connector.admin_data_source.users_list = AsyncMock(side_effect=[
            {
                "users": [{"id": "u1", "primaryEmail": "a@t.com", "name": {"fullName": "A"}, "suspended": False}],
                "nextPageToken": "p2",
            },
            {
                "users": [{"id": "u2", "primaryEmail": "b@t.com", "name": {"fullName": "B"}, "suspended": False}],
                "nextPageToken": "p3",
            },
            {
                "users": [{"id": "u3", "primaryEmail": "c@t.com", "name": {"fullName": "C"}, "suspended": False}],
            },
        ])
        await connector._sync_users()
        assert connector.admin_data_source.users_list.call_count == 3
        assert len(connector.synced_users) == 3


# ---------------------------------------------------------------------------
# _sync_user_groups deep paths
# ---------------------------------------------------------------------------

class TestSyncUserGroupsDeep:
    """Deep tests for _sync_user_groups."""

    async def test_group_without_email_skipped(self, connector):
        """Group without email is skipped in _process_group."""
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"id": "g1", "name": "No Email Group"}],
        })
        connector.admin_data_source.members_list = AsyncMock(return_value={"members": []})
        await connector._sync_user_groups()
        connector.data_entities_processor.on_new_user_groups.assert_not_called()

    async def test_group_members_pagination(self, connector):
        """Group members fetched across multiple pages."""
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"id": "g1", "email": "group@example.com", "name": "Group"}],
        })
        connector.admin_data_source.members_list = AsyncMock(side_effect=[
            {
                "members": [{"type": "USER", "id": "u1", "email": "a@t.com"}],
                "nextPageToken": "mp2",
            },
            {
                "members": [{"type": "USER", "id": "u2", "email": "b@t.com"}],
            },
        ])
        await connector._sync_user_groups()
        assert connector.admin_data_source.members_list.call_count == 2
        connector.data_entities_processor.on_new_user_groups.assert_called_once()

    async def test_group_member_without_email_skipped(self, connector):
        """Group members without email are skipped."""
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"id": "g1", "email": "group@example.com", "name": "Group"}],
        })
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [
                {"type": "USER", "id": "u1", "email": ""},
                {"type": "USER", "id": "u2", "email": "good@t.com"},
            ],
        })
        await connector._sync_user_groups()
        call_args = connector.data_entities_processor.on_new_user_groups.call_args[0][0]
        _, app_users = call_args[0]
        assert len(app_users) == 1

    async def test_group_member_matched_to_synced_user(self, connector):
        """Group member is matched to a synced user for full details."""
        connector.synced_users = [_make_app_user("member@t.com", "u1", "Full Name")]
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"id": "g1", "email": "group@example.com", "name": "Group"}],
        })
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": "member@t.com"}],
        })
        await connector._sync_user_groups()
        call_args = connector.data_entities_processor.on_new_user_groups.call_args[0][0]
        _, app_users = call_args[0]
        assert app_users[0].full_name == "Full Name"

    async def test_group_with_bad_creation_time(self, connector):
        """Group with bad creation time still processes."""
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{
                "id": "g1", "email": "group@example.com",
                "name": "Group", "creationTime": "invalid"
            }],
        })
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": "a@t.com"}],
        })
        await connector._sync_user_groups()
        connector.data_entities_processor.on_new_user_groups.assert_called_once()

    async def test_groups_pagination(self, connector):
        """Groups are fetched across multiple pages."""
        connector.admin_data_source.groups_list = AsyncMock(side_effect=[
            {
                "groups": [{"id": "g1", "email": "g1@t.com", "name": "G1"}],
                "nextPageToken": "gp2",
            },
            {
                "groups": [{"id": "g2", "email": "g2@t.com", "name": "G2"}],
            },
        ])
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": "a@t.com"}],
        })
        await connector._sync_user_groups()
        assert connector.admin_data_source.groups_list.call_count == 2

    async def test_group_processing_error_continues(self, connector):
        """Error processing one group does not stop others."""
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [
                {"id": "g1", "email": "g1@t.com", "name": "G1"},
                {"id": "g2", "email": "g2@t.com", "name": "G2"},
            ],
        })
        # First group fails on members_list, second succeeds
        connector.admin_data_source.members_list = AsyncMock(side_effect=[
            RuntimeError("API fail"),
            {"members": [{"type": "USER", "id": "u1", "email": "a@t.com"}]},
        ])
        await connector._sync_user_groups()
        # Second group still processed
        connector.data_entities_processor.on_new_user_groups.assert_called_once()


# ---------------------------------------------------------------------------
# _sync_record_groups deep paths
# ---------------------------------------------------------------------------

class TestSyncRecordGroupsDeep:
    """Deep tests for shared drive and personal drive record group sync."""

    async def test_shared_drive_pagination(self, connector):
        """Shared drives fetched across multiple pages."""
        connector.drive_data_source.drives_list = AsyncMock(side_effect=[
            {
                "drives": [{"id": "sd1", "name": "SD1", "createdTime": "2024-01-01T00:00:00Z"}],
                "nextPageToken": "dp2",
            },
            {
                "drives": [{"id": "sd2", "name": "SD2", "createdTime": "2024-01-01T00:00:00Z"}],
            },
        ])
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "organizer", "type": "user", "emailAddress": "admin@t.com"}],
        })
        connector.synced_users = []
        await connector._sync_record_groups()
        assert connector.drive_data_source.drives_list.call_count == 2

    async def test_shared_drive_error_continues(self, connector):
        """Error syncing one shared drive continues to personal drives."""
        connector.drive_data_source.drives_list = AsyncMock(return_value={
            "drives": [{"id": "sd1", "name": "SD1"}],
        })
        connector.drive_data_source.permissions_list = AsyncMock(
            side_effect=RuntimeError("permission error")
        )
        connector.synced_users = []
        # Should not raise despite permission error
        await connector._sync_record_groups()

    async def test_personal_drive_record_groups_created(self, connector):
        """Personal drive record groups are created for each synced user."""
        connector.drive_data_source.drives_list = AsyncMock(return_value={"drives": []})
        user = _make_app_user("user@t.com", "u1", "Test User")
        connector.synced_users = [user]

        with patch.object(connector, "_create_personal_record_group", new_callable=AsyncMock) as mock_create:
            await connector._sync_record_groups()
            mock_create.assert_called_once_with(user)

    async def test_personal_drive_error_continues(self, connector):
        """Error creating personal drive for one user continues to next."""
        connector.drive_data_source.drives_list = AsyncMock(return_value={"drives": []})
        user1 = _make_app_user("u1@t.com", "u1", "U1")
        user2 = _make_app_user("u2@t.com", "u2", "U2")
        connector.synced_users = [user1, user2]

        call_count = 0

        async def mock_create(user):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first fails")

        with patch.object(connector, "_create_personal_record_group", side_effect=mock_create):
            await connector._sync_record_groups()
        assert call_count == 2


# ---------------------------------------------------------------------------
# _process_drive_item deep paths
# ---------------------------------------------------------------------------

class TestProcessDriveItemDeep:
    """Deep tests for _process_drive_item covering update detection and edge cases."""

    async def test_returns_none_for_no_id(self, connector):
        """File without ID returns None."""
        result = await connector._process_drive_item(
            metadata={}, user_id="u1", user_email="user@t.com",
            drive_id="d1", is_shared_drive=False,
        )
        assert result is None

    async def test_existing_record_name_change_detected(self, connector):
        """Detects name change on existing record."""
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "old-name.txt"
        existing.external_revision_id = "rev-1"
        existing.parent_external_record_id = "parent-1"
        existing.external_record_group_id = "d1"
        existing.version = 1
        existing.indexing_status = "completed"
        existing.extraction_status = "completed"

        connector.data_store_provider = _make_mock_data_store_provider(existing)
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "reader", "type": "user", "emailAddress": "u@t.com"}],
        })

        metadata = _make_file_metadata(file_id="f1", name="new-name.txt", parents=["parent-1"])
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="u@t.com",
            drive_id="d1", is_shared_drive=False,
        )
        assert result is not None
        assert result.is_updated is True
        assert result.metadata_changed is True

    async def test_existing_record_revision_change_detected(self, connector):
        """Detects content change via revision ID."""
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "test.txt"
        existing.external_revision_id = "old-rev"
        existing.parent_external_record_id = "parent-1"
        existing.external_record_group_id = "d1"
        existing.version = 1
        existing.indexing_status = "completed"
        existing.extraction_status = "completed"

        connector.data_store_provider = _make_mock_data_store_provider(existing)
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "reader", "type": "user", "emailAddress": "u@t.com"}],
        })

        metadata = _make_file_metadata(
            file_id="f1", name="test.txt", parents=["parent-1"],
            head_revision_id="new-rev",
        )
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="u@t.com",
            drive_id="d1", is_shared_drive=False,
        )
        assert result is not None
        assert result.content_changed is True

    async def test_folder_detected_correctly(self, connector):
        """Folder MIME type sets is_file to False."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "reader", "type": "user", "emailAddress": "u@t.com"}],
        })
        metadata = _make_file_metadata(
            file_id="folder-1", name="My Folder",
            mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            parents=["d1"],
        )
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="u@t.com",
            drive_id="d1", is_shared_drive=True,
        )
        assert result is not None
        assert result.record.is_file is False

    async def test_version_fallback_when_no_head_revision(self, connector):
        """Uses version field when headRevisionId is not present."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [],
        })
        metadata = _make_file_metadata(file_id="f1", parents=["d1"])
        metadata.pop("headRevisionId")
        metadata["version"] = "42"
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="u@t.com",
            drive_id="d1", is_shared_drive=False,
        )
        assert result is not None
        assert result.record.external_revision_id == "42"

    async def test_file_extension_from_name(self, connector):
        """Extension extracted from name when fileExtension not present."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [],
        })
        metadata = _make_file_metadata(file_id="f1", name="report.pdf", parents=["d1"])
        metadata.pop("fileExtension", None)
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="u@t.com",
            drive_id="d1", is_shared_drive=False,
        )
        assert result is not None
        assert result.record.extension == "pdf"

    async def test_parent_at_root_is_none(self, connector):
        """Parent set to None when parents[0] == drive_id."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [],
        })
        metadata = _make_file_metadata(file_id="f1", parents=["d1"])
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="u@t.com",
            drive_id="d1", is_shared_drive=False,
        )
        assert result is not None
        assert result.record.parent_external_record_id is None
        assert result.record.parent_record_type is None

    async def test_exception_returns_none(self, connector):
        """Exception during processing returns None."""
        # Make data_store_provider raise
        provider = MagicMock()

        @asynccontextmanager
        async def _failing_tx():
            raise RuntimeError("DB error")
            yield  # noqa: unreachable

        provider.transaction = _failing_tx
        connector.data_store_provider = provider

        metadata = _make_file_metadata(file_id="f1", parents=["d1"])
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="u@t.com",
            drive_id="d1", is_shared_drive=False,
        )
        assert result is None

    async def test_fallback_permissions_adds_to_existing_record(self, connector):
        """Fallback permissions are added to an existing record."""
        from googleapiclient.errors import HttpError

        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "test.txt"
        existing.external_revision_id = "rev-1"
        existing.parent_external_record_id = "parent-1"
        existing.external_record_group_id = "d1"
        existing.version = 1
        existing.indexing_status = "completed"
        existing.extraction_status = "completed"

        connector.data_store_provider = _make_mock_data_store_provider(existing)

        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "insufficientFilePermissions"}]
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        metadata = _make_file_metadata(file_id="f1", parents=["parent-1"])
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="u@t.com",
            drive_id="d1", is_shared_drive=False,
        )
        assert result is not None
        connector.data_entities_processor.add_permission_to_record.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_record_updates deep paths
# ---------------------------------------------------------------------------

class TestHandleRecordUpdatesDeep:
    """Deep tests for _handle_record_updates covering all update types."""

    async def test_deleted_record(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        update = RecordUpdate(
            record=None, is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="file-123",
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_called_once_with(
            record_id="file-123"
        )

    async def test_new_record_no_op(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        record.record_name = "new.txt"
        update = RecordUpdate(
            record=record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="file-1",
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_not_called()
        connector.data_entities_processor.on_record_metadata_update.assert_not_called()

    async def test_metadata_change(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        record.record_name = "renamed.txt"
        update = RecordUpdate(
            record=record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
            external_record_id="file-1",
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_called_once()

    async def test_permissions_change(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        record.record_name = "file.txt"
        new_perms = [MagicMock()]
        update = RecordUpdate(
            record=record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=True,
            new_permissions=new_perms, external_record_id="file-1",
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_updated_record_permissions.assert_called_once()

    async def test_content_change(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        record.record_name = "file.txt"
        update = RecordUpdate(
            record=record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=False, content_changed=True, permissions_changed=False,
            external_record_id="file-1",
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_content_update.assert_called_once()

    async def test_all_changes_combined(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        record.record_name = "file.txt"
        update = RecordUpdate(
            record=record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=True, permissions_changed=True,
            new_permissions=[MagicMock()], external_record_id="file-1",
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_called_once()
        connector.data_entities_processor.on_updated_record_permissions.assert_called_once()
        connector.data_entities_processor.on_record_content_update.assert_called_once()

    async def test_exception_handled_gracefully(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        connector.data_entities_processor.on_record_deleted = AsyncMock(
            side_effect=RuntimeError("DB error")
        )
        update = RecordUpdate(
            record=None, is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="file-1",
        )
        # Should not raise
        await connector._handle_record_updates(update)


# ---------------------------------------------------------------------------
# _process_users_in_batches
# ---------------------------------------------------------------------------

class TestProcessUsersInBatches:
    """Deep tests for batch processing of users."""

    async def test_filters_inactive_users(self, connector):
        """Only active users are processed."""
        active_user = MagicMock()
        active_user.email = "active@t.com"
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[active_user]
        )

        user_active = _make_app_user("active@t.com", "u1")
        user_inactive = _make_app_user("inactive@t.com", "u2")

        with patch.object(connector, "_run_sync_with_yield", new_callable=AsyncMock) as mock_sync:
            await connector._process_users_in_batches([user_active, user_inactive])
            mock_sync.assert_called_once_with(user_active)

    async def test_empty_users_list(self, connector):
        """Empty users list does not crash."""
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
        with patch.object(connector, "_run_sync_with_yield", new_callable=AsyncMock) as mock_sync:
            await connector._process_users_in_batches([])
            mock_sync.assert_not_called()


# ---------------------------------------------------------------------------
# _fetch_permissions deep paths
# ---------------------------------------------------------------------------

class TestFetchPermissionsDeep:
    """Deep tests for permission fetching edge cases."""

    async def test_pagination_in_permissions(self, connector):
        """Permissions fetched across multiple pages."""
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=[
            {
                "permissions": [{"id": "p1", "role": "reader", "type": "user", "emailAddress": "a@t.com"}],
                "nextPageToken": "pp2",
            },
            {
                "permissions": [{"id": "p2", "role": "writer", "type": "user", "emailAddress": "b@t.com"}],
            },
        ])
        perms, is_fallback = await connector._fetch_permissions("file-1", is_drive=False)
        assert len(perms) == 2
        assert is_fallback is False

    async def test_anyone_with_link_adds_user_fallback(self, connector):
        """Anyone with link permission adds a user fallback."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "anyone", "emailAddress": None},
            ],
        })
        perms, is_fallback = await connector._fetch_permissions(
            "file-1", is_drive=False, user_email="user@t.com"
        )
        assert is_fallback is True
        assert len(perms) == 1
        assert perms[0].email == "user@t.com"

    async def test_anyone_with_link_user_already_has_permission(self, connector):
        """Anyone with link does not duplicate when user already has permission."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "anyone", "emailAddress": None},
                {"id": "p2", "role": "writer", "type": "user", "emailAddress": "user@t.com"},
            ],
        })
        perms, is_fallback = await connector._fetch_permissions(
            "file-1", is_drive=False, user_email="user@t.com"
        )
        assert is_fallback is False  # Not fallback since user has explicit permission
        assert len(perms) == 2

    async def test_drive_403_raises(self, connector):
        """403 error on drive raises exception."""
        from googleapiclient.errors import HttpError
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = []
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)
        with pytest.raises(HttpError):
            await connector._fetch_permissions("drive-1", is_drive=True)

    async def test_file_non_403_returns_empty(self, connector):
        """Non-403 HttpError on file returns empty list."""
        from googleapiclient.errors import HttpError
        mock_resp = MagicMock()
        mock_resp.status = 500
        http_error = HttpError(mock_resp, b"server error")
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)
        perms, is_fallback = await connector._fetch_permissions("file-1", is_drive=False)
        assert perms == []
        assert is_fallback is False

    async def test_generic_exception_on_drive_raises(self, connector):
        """Generic exception on drive raises."""
        connector.drive_data_source.permissions_list = AsyncMock(
            side_effect=RuntimeError("network error")
        )
        with pytest.raises(RuntimeError):
            await connector._fetch_permissions("drive-1", is_drive=True)

    async def test_generic_exception_on_file_returns_empty(self, connector):
        """Generic exception on file returns empty."""
        connector.drive_data_source.permissions_list = AsyncMock(
            side_effect=RuntimeError("network error")
        )
        perms, is_fallback = await connector._fetch_permissions("file-1", is_drive=False)
        assert perms == []

    async def test_group_permission_maps_external_id_to_email(self, connector):
        """Group permission uses email as external_id."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "group", "emailAddress": "group@t.com"},
            ],
        })
        perms, _ = await connector._fetch_permissions("file-1", is_drive=False)
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.GROUP
        assert perms[0].external_id == "group@t.com"


# ---------------------------------------------------------------------------
# _parse_datetime and _pass_date_filters
# ---------------------------------------------------------------------------

class TestParseDatetimeDeep:
    """Tests for _parse_datetime edge cases."""

    def test_none_returns_none(self, connector):
        assert connector._parse_datetime(None) is None

    def test_string_iso(self, connector):
        result = connector._parse_datetime("2025-01-01T00:00:00Z")
        assert isinstance(result, int)
        assert result > 0

    def test_invalid_string_returns_none(self, connector):
        assert connector._parse_datetime("not-a-date") is None

    def test_datetime_object(self, connector):
        from datetime import datetime, timezone
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = connector._parse_datetime(dt)
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# _handle_drive_error
# ---------------------------------------------------------------------------

class TestHandleDriveErrorDeep:
    """Additional _handle_drive_error cases."""

    async def test_403_non_membership_reason(self, connector):
        """403 without teamDriveMembershipRequired still returns True."""
        from googleapiclient.errors import HttpError
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "someOtherReason"}]
        result = await connector._handle_drive_error(http_error, "Drive", "d1")
        assert result is True

    async def test_non_http_error(self, connector):
        """Non-HttpError returns True (break)."""
        result = await connector._handle_drive_error(ValueError("bad"), "Drive", "d1")
        assert result is True


# ---------------------------------------------------------------------------
# test_connection_and_access
# ---------------------------------------------------------------------------

class TestTestConnection:
    """Tests for test_connection_and_access."""

    async def test_returns_true_when_all_initialized(self, connector):
        result = await connector.test_connection_and_access()
        assert result is True

    async def test_returns_false_when_drive_source_none(self, connector):
        connector.drive_data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    async def test_returns_false_when_admin_source_none(self, connector):
        connector.admin_data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    async def test_returns_false_when_clients_none(self, connector):
        connector.drive_client = None
        connector.admin_client = None
        result = await connector.test_connection_and_access()
        assert result is False
