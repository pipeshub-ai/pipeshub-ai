"""Extended tests for GoogleDriveTeamConnector covering more uncovered code paths."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, OriginTypes, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import FilterCollection, FilterOperator
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
    log = logging.getLogger("test_drive_team_ext")
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


def _make_google_user(email="user@example.com", user_id="guser-1", full_name="Test User",
                      suspended=False, creation_time="2024-01-01T00:00:00.000Z",
                      organizations=None, given_name="Test", family_name="User"):
    user = {
        "id": user_id,
        "primaryEmail": email,
        "name": {"fullName": full_name, "givenName": given_name, "familyName": family_name},
        "suspended": suspended,
        "creationTime": creation_time,
        "organizations": organizations if organizations is not None else [{"title": "Engineer"}],
    }
    return user


def _make_file_metadata(
    file_id="file-1", name="test.txt", mime_type="text/plain",
    created_time="2025-01-01T00:00:00Z", modified_time="2025-01-15T00:00:00Z",
    parents=None, shared=False, head_revision_id="rev-1", file_extension="txt",
    size=1024, owners=None, version=None,
):
    meta = {
        "id": file_id, "name": name, "mimeType": mime_type,
        "createdTime": created_time, "modifiedTime": modified_time,
        "shared": shared, "headRevisionId": head_revision_id,
        "fileExtension": file_extension, "size": size,
        "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
    }
    if parents is not None:
        meta["parents"] = parents
    if owners is not None:
        meta["owners"] = owners
    if version is not None:
        meta["version"] = version
    return meta


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
            scope="personal",
            created_by="test-user-id",
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
# _sync_users - extended coverage
# ---------------------------------------------------------------------------

class TestSyncUsersExtended:
    """Extended tests for enterprise user sync covering edge cases."""

    async def test_user_with_name_from_given_family(self, connector):
        """User without fullName gets name from givenName + familyName."""
        user = _make_google_user()
        user["name"]["fullName"] = ""
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert users[0].full_name == "Test User"

    async def test_user_name_falls_back_to_email(self, connector):
        """User without any name info falls back to email."""
        user = _make_google_user()
        user["name"] = {"fullName": "", "givenName": "", "familyName": ""}
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert users[0].full_name == "user@example.com"

    async def test_user_title_from_organizations(self, connector):
        """User title extracted from organizations."""
        user = _make_google_user(organizations=[{"title": "Senior Engineer"}])
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert users[0].title == "Senior Engineer"

    async def test_user_with_no_organizations(self, connector):
        """User with empty organizations list."""
        user = _make_google_user(organizations=[])
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert users[0].title is None

    async def test_user_creation_time_parsed(self, connector):
        """User creation time is parsed to epoch ms."""
        user = _make_google_user(creation_time="2024-06-15T12:00:00.000Z")
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert users[0].source_created_at is not None

    async def test_user_with_bad_creation_time(self, connector):
        """User with invalid creation time still syncs."""
        user = _make_google_user(creation_time="not-a-date")
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(users) == 1

    async def test_user_processing_error_continues(self, connector):
        """Error processing one user doesn't stop sync."""
        good_user = _make_google_user("good@example.com", "u1")
        # Create a user that will cause an error during AppUser construction
        bad_user = {"id": "bad-user"}  # Missing required fields like primaryEmail, name
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [bad_user, good_user],
        })
        await connector._sync_users()
        # Should still process the good user
        users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(users) >= 1

    async def test_sync_users_page_error_raises(self, connector):
        """Error fetching a user page raises."""
        connector.admin_data_source.users_list = AsyncMock(side_effect=Exception("API error"))
        with pytest.raises(Exception, match="API error"):
            await connector._sync_users()


# ---------------------------------------------------------------------------
# _sync_user_groups - extended coverage
# ---------------------------------------------------------------------------

class TestSyncUserGroupsExtended:
    """Extended tests for group sync."""

    async def test_raises_when_no_admin_source(self, connector):
        connector.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector._sync_user_groups()

    async def test_group_pagination(self, connector):
        """Group sync handles pagination."""
        connector.admin_data_source.groups_list = AsyncMock(side_effect=[
            {"groups": [{"email": "g1@example.com", "name": "G1", "creationTime": "2024-01-01T00:00:00Z"}], "nextPageToken": "p2"},
            {"groups": [{"email": "g2@example.com", "name": "G2"}]},
        ])
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": "a@example.com"}],
        })
        await connector._sync_user_groups()
        assert connector.admin_data_source.groups_list.call_count == 2

    async def test_group_with_no_email_skipped(self, connector):
        """Group without email is skipped."""
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"name": "NoEmail"}],
        })
        await connector._sync_user_groups()
        connector.data_entities_processor.on_new_user_groups.assert_not_called()

    async def test_group_member_without_email_skipped(self, connector):
        """Group member without email is skipped."""
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"email": "g1@example.com", "name": "G1"}],
        })
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": ""}],
        })
        await connector._sync_user_groups()
        connector.data_entities_processor.on_new_user_groups.assert_not_called()

    async def test_group_name_fallback_to_email(self, connector):
        """Group without name uses email as fallback."""
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"email": "g1@example.com", "name": ""}],
        })
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": "a@example.com"}],
        })
        await connector._sync_user_groups()
        connector.data_entities_processor.on_new_user_groups.assert_called_once()

    async def test_group_member_matches_synced_user(self, connector):
        """Group member details enriched from synced users."""
        synced_user = AppUser(
            app_name="DRIVE WORKSPACE",
            connector_id="drive-team-1",
            source_user_id="u1",
            email="alice@example.com",
            full_name="Alice Wonderland",
            source_created_at=1000,
        )
        connector.synced_users = [synced_user]
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"email": "g1@example.com", "name": "G1"}],
        })
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": "alice@example.com"}],
        })
        await connector._sync_user_groups()
        call_args = connector.data_entities_processor.on_new_user_groups.call_args[0][0]
        _, app_users = call_args[0]
        assert app_users[0].full_name == "Alice Wonderland"

    async def test_group_creation_time_parsed(self, connector):
        """Group with bad creation time still processes."""
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"email": "g1@example.com", "name": "G1", "creationTime": "invalid"}],
        })
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": "a@example.com"}],
        })
        await connector._sync_user_groups()
        connector.data_entities_processor.on_new_user_groups.assert_called_once()

    async def test_fetch_group_members_pagination(self, connector):
        """_fetch_group_members handles pagination."""
        connector.admin_data_source.members_list = AsyncMock(side_effect=[
            {"members": [{"type": "USER", "id": "u1"}], "nextPageToken": "p2"},
            {"members": [{"type": "USER", "id": "u2"}]},
        ])
        result = await connector._fetch_group_members("g1@example.com")
        assert len(result) == 2

    async def test_fetch_group_members_empty(self, connector):
        """_fetch_group_members returns empty list when no members."""
        connector.admin_data_source.members_list = AsyncMock(return_value={"members": []})
        result = await connector._fetch_group_members("g1@example.com")
        assert len(result) == 0

    async def test_fetch_group_members_error_raises(self, connector):
        """_fetch_group_members raises on API error."""
        connector.admin_data_source.members_list = AsyncMock(side_effect=Exception("API error"))
        with pytest.raises(Exception, match="API error"):
            await connector._fetch_group_members("g1@example.com")


# ---------------------------------------------------------------------------
# _fetch_permissions - extended coverage
# ---------------------------------------------------------------------------

class TestFetchPermissionsExtended:
    """Extended tests for _fetch_permissions."""

    async def test_permission_pagination(self, connector):
        """Handles pagination in permission listing."""
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=[
            {"permissions": [{"id": "p1", "role": "owner", "type": "user", "emailAddress": "a@example.com"}], "nextPageToken": "p2"},
            {"permissions": [{"id": "p2", "role": "reader", "type": "user", "emailAddress": "b@example.com"}]},
        ])
        perms, is_fallback = await connector._fetch_permissions("file-1", is_drive=False)
        assert len(perms) == 2
        assert is_fallback is False

    async def test_anyone_permission_adds_fallback_for_user(self, connector):
        """Anyone permission creates fallback permission for current user."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "anyone", "emailAddress": None},
            ],
        })
        perms, is_fallback = await connector._fetch_permissions(
            "file-1", is_drive=False, user_email="user@example.com"
        )
        assert is_fallback is True
        assert len(perms) == 1
        assert perms[0].email == "user@example.com"

    async def test_anyone_permission_no_fallback_if_user_exists(self, connector):
        """Anyone permission doesn't create duplicate if user already has permission."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "anyone", "emailAddress": None},
                {"id": "p2", "role": "writer", "type": "user", "emailAddress": "user@example.com"},
            ],
        })
        perms, is_fallback = await connector._fetch_permissions(
            "file-1", is_drive=False, user_email="user@example.com"
        )
        assert is_fallback is False
        assert len(perms) == 2

    async def test_drive_permissions_with_domain_admin(self, connector):
        """Drive permissions use domain admin access."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "organizer", "type": "group", "emailAddress": "group@example.com"},
            ],
        })
        perms, _ = await connector._fetch_permissions("drive-1", is_drive=True)
        call_kwargs = connector.drive_data_source.permissions_list.call_args[1]
        assert call_kwargs.get("useDomainAdminAccess") is True

    async def test_403_for_drive_raises(self, connector):
        """403 for drives raises (no fallback)."""
        from googleapiclient.errors import HttpError
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)
        with pytest.raises(HttpError):
            await connector._fetch_permissions("drive-1", is_drive=True)

    async def test_403_without_insufficient_permissions_reason(self, connector):
        """403 without specific reason returns empty perms."""
        from googleapiclient.errors import HttpError
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "otherReason"}]
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)
        perms, is_fallback = await connector._fetch_permissions(
            "file-1", is_drive=False, user_email="user@example.com"
        )
        assert is_fallback is False
        assert len(perms) == 0

    async def test_non_403_http_error_for_file(self, connector):
        """Non-403 HttpError for files returns empty perms."""
        from googleapiclient.errors import HttpError
        mock_resp = MagicMock()
        mock_resp.status = 500
        http_error = HttpError(mock_resp, b"server error")
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)
        perms, is_fallback = await connector._fetch_permissions("file-1", is_drive=False)
        assert len(perms) == 0

    async def test_generic_error_for_file_returns_empty(self, connector):
        """Generic error for files returns empty perms."""
        connector.drive_data_source.permissions_list = AsyncMock(
            side_effect=Exception("network error")
        )
        perms, is_fallback = await connector._fetch_permissions("file-1", is_drive=False)
        assert len(perms) == 0

    async def test_generic_error_for_drive_raises(self, connector):
        """Generic error for drives raises."""
        connector.drive_data_source.permissions_list = AsyncMock(
            side_effect=Exception("network error")
        )
        with pytest.raises(Exception, match="network error"):
            await connector._fetch_permissions("drive-1", is_drive=True)

    async def test_group_permission_entity_type(self, connector):
        """Group permissions mapped correctly."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "group", "emailAddress": "group@example.com"},
            ],
        })
        perms, _ = await connector._fetch_permissions("file-1", is_drive=False)
        assert perms[0].entity_type == EntityType.GROUP
        assert perms[0].external_id == "group@example.com"

    async def test_domain_permission_entity_type(self, connector):
        """Domain permission type."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "domain", "domain": "example.com"},
            ],
        })
        perms, _ = await connector._fetch_permissions("file-1", is_drive=False)
        assert perms[0].entity_type == EntityType.DOMAIN

    async def test_custom_drive_data_source(self, connector):
        """Custom drive data source is used if provided."""
        custom_ds = AsyncMock()
        custom_ds.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "owner", "type": "user", "emailAddress": "owner@example.com"},
            ],
        })
        perms, _ = await connector._fetch_permissions("file-1", is_drive=False, drive_data_source=custom_ds)
        assert len(perms) == 1
        custom_ds.permissions_list.assert_called_once()


# ---------------------------------------------------------------------------
# _parse_datetime
# ---------------------------------------------------------------------------

class TestParseDatetime:
    """Tests for _parse_datetime."""

    def test_none_input(self, connector):
        assert connector._parse_datetime(None) is None

    def test_empty_string(self, connector):
        assert connector._parse_datetime("") is None

    def test_valid_iso_string(self, connector):
        result = connector._parse_datetime("2025-01-01T00:00:00Z")
        assert result is not None
        assert isinstance(result, int)

    def test_datetime_object(self, connector):
        from datetime import datetime, timezone
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = connector._parse_datetime(dt)
        assert result is not None

    def test_invalid_string(self, connector):
        assert connector._parse_datetime("not-a-date") is None


# ---------------------------------------------------------------------------
# _pass_date_filters - extended
# ---------------------------------------------------------------------------

class TestPassDateFiltersExtended:
    """Extended tests for date filtering."""

    def test_created_before_filter_blocks(self, connector):
        """File created after the 'before' date is blocked."""
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = (None, "2024-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_filter if k.value == "created" else None)
        metadata = {"mimeType": "text/plain", "createdTime": "2025-06-01T00:00:00Z"}
        assert connector._pass_date_filters(metadata) is False

    def test_created_after_filter_blocks(self, connector):
        """File created before the 'after' date is blocked."""
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2025-06-01T00:00:00Z", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_filter if k.value == "created" else None)
        metadata = {"mimeType": "text/plain", "createdTime": "2024-01-01T00:00:00Z"}
        assert connector._pass_date_filters(metadata) is False

    def test_modified_before_filter_blocks(self, connector):
        """File modified after the 'before' date is blocked."""
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = (None, "2024-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_filter if k.value == "modified" else None)
        metadata = {"mimeType": "text/plain", "modifiedTime": "2025-06-01T00:00:00Z"}
        assert connector._pass_date_filters(metadata) is False

    def test_modified_after_filter_blocks(self, connector):
        """File modified before the 'after' date is blocked."""
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2025-06-01T00:00:00Z", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_filter if k.value == "modified" else None)
        metadata = {"mimeType": "text/plain", "modifiedTime": "2024-01-01T00:00:00Z"}
        assert connector._pass_date_filters(metadata) is False

    def test_file_passes_both_filters(self, connector):
        """File within both date ranges passes."""
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        metadata = {"mimeType": "text/plain", "createdTime": "2025-06-01T00:00:00Z", "modifiedTime": "2025-06-01T00:00:00Z"}
        assert connector._pass_date_filters(metadata) is True


# ---------------------------------------------------------------------------
# _pass_extension_filter - extended
# ---------------------------------------------------------------------------

class TestPassExtensionFilterExtended:
    """Extended tests for extension filtering."""

    def test_google_docs_mime_with_in_operator(self, connector):
        """Google Docs mimeType with IN operator."""
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = [MimeTypes.GOOGLE_DOCS.value]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator.return_value = mock_operator
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        metadata = {"mimeType": MimeTypes.GOOGLE_DOCS.value}
        assert connector._pass_extension_filter(metadata) is True

    def test_google_docs_mime_not_in_list(self, connector):
        """Google Docs mimeType not in allowed list with IN operator."""
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = [MimeTypes.GOOGLE_SHEETS.value]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator.return_value = mock_operator
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        metadata = {"mimeType": MimeTypes.GOOGLE_DOCS.value}
        assert connector._pass_extension_filter(metadata) is False

    def test_google_docs_not_in_operator(self, connector):
        """Google Docs with NOT_IN operator."""
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = [MimeTypes.GOOGLE_DOCS.value]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.NOT_IN
        mock_filter.get_operator.return_value = mock_operator
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        metadata = {"mimeType": MimeTypes.GOOGLE_DOCS.value}
        assert connector._pass_extension_filter(metadata) is False

    def test_file_extension_in_list(self, connector):
        """Regular file extension in allowed list."""
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "txt"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator.return_value = mock_operator
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        metadata = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert connector._pass_extension_filter(metadata) is True

    def test_file_without_extension_in_mode(self, connector):
        """File without extension fails IN filter."""
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator.return_value = mock_operator
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        metadata = {"mimeType": "application/octet-stream", "name": "noext"}
        assert connector._pass_extension_filter(metadata) is False

    def test_file_without_extension_not_in_mode(self, connector):
        """File without extension passes NOT_IN filter."""
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.NOT_IN
        mock_filter.get_operator.return_value = mock_operator
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        metadata = {"mimeType": "application/octet-stream", "name": "noext"}
        assert connector._pass_extension_filter(metadata) is True

    def test_extension_from_filename(self, connector):
        """Extension extracted from filename when fileExtension not set."""
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator.return_value = mock_operator
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        metadata = {"mimeType": "application/pdf", "name": "document.pdf"}
        assert connector._pass_extension_filter(metadata) is True

    def test_non_list_filter_value_allows(self, connector):
        """Non-list filter value allows all files."""
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = "not-a-list"
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        metadata = {"mimeType": "text/plain"}
        assert connector._pass_extension_filter(metadata) is True


# ---------------------------------------------------------------------------
# _process_drive_item (team) - extended
# ---------------------------------------------------------------------------

class TestTeamProcessDriveItemExtended:
    """Extended tests for team connector _process_drive_item."""

    async def test_no_file_id_returns_none(self, connector):
        result = await connector._process_drive_item(
            metadata={"name": "orphan"}, user_id="u1",
            user_email="user@example.com", drive_id="drive-1", is_shared_drive=False,
        )
        assert result is None

    async def test_existing_record_with_name_change(self, connector):
        """Existing record with name change detected."""
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "old_name.txt"
        existing.external_revision_id = "rev-1"
        existing.external_record_group_id = "drive-1"
        existing.parent_external_record_id = None
        existing.version = 0
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.extraction_status = ProgressStatus.COMPLETED.value
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "owner", "type": "user", "emailAddress": "user@example.com"}],
        })
        metadata = _make_file_metadata(name="new_name.txt", parents=None)
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com",
            drive_id="drive-1", is_shared_drive=False,
        )
        assert result.is_updated is True
        assert result.metadata_changed is True

    async def test_existing_record_with_content_change(self, connector):
        """Existing record with content change detected."""
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "test.txt"
        existing.external_revision_id = "old-rev"
        existing.external_record_group_id = "drive-1"
        existing.parent_external_record_id = None
        existing.version = 0
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.extraction_status = ProgressStatus.COMPLETED.value
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "owner", "type": "user", "emailAddress": "user@example.com"}],
        })
        metadata = _make_file_metadata(head_revision_id="new-rev", parents=None)
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com",
            drive_id="drive-1", is_shared_drive=False,
        )
        assert result.content_changed is True

    async def test_existing_no_content_change_preserves_status(self, connector):
        """No content change preserves indexing status."""
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "test.txt"
        existing.external_revision_id = "rev-1"
        existing.external_record_group_id = "drive-1"
        existing.parent_external_record_id = None
        existing.version = 0
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.extraction_status = ProgressStatus.COMPLETED.value
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "owner", "type": "user", "emailAddress": "user@example.com"}],
        })
        metadata = _make_file_metadata(parents=None)
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com",
            drive_id="drive-1", is_shared_drive=False,
        )
        assert result.record.indexing_status == ProgressStatus.COMPLETED.value

    async def test_shared_with_me_external_group_id_is_none(self, connector):
        """Shared-with-me files get None external_record_group_id."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "reader", "type": "user", "emailAddress": "reader@example.com"}],
        })
        metadata = _make_file_metadata(
            shared=True, owners=[{"emailAddress": "owner@other.com"}], parents=["p1"],
        )
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="reader@example.com",
            drive_id="drive-1", is_shared_drive=False,
        )
        assert result.record.external_record_group_id is None
        assert result.record.is_shared_with_me is True

    async def test_permission_fetch_failure_continues(self, connector):
        """Permission fetch failure still returns record update."""
        connector.drive_data_source.permissions_list = AsyncMock(
            side_effect=Exception("perm error")
        )
        metadata = _make_file_metadata(parents=["p1"])
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com",
            drive_id="drive-1", is_shared_drive=True,
        )
        assert result is not None
        # _fetch_permissions returns ([], False) for non-drive generic errors (doesn't raise),
        # so permissions_changed is True (non-fallback)
        assert result.permissions_changed is True

    async def test_fallback_permissions_adds_to_existing_record(self, connector):
        """Fallback permissions are added to existing record."""
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "test.txt"
        existing.external_revision_id = "rev-1"
        existing.external_record_group_id = "drive-1"
        existing.parent_external_record_id = None
        existing.version = 0
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.extraction_status = ProgressStatus.COMPLETED.value
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        from googleapiclient.errors import HttpError
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "insufficientFilePermissions"}]
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        metadata = _make_file_metadata(parents=None)
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com",
            drive_id="drive-1", is_shared_drive=False,
        )
        assert result is not None
        connector.data_entities_processor.add_permission_to_record.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_drive_error - extended
# ---------------------------------------------------------------------------

class TestHandleDriveErrorExtended:
    """Extended tests for _handle_drive_error."""

    async def test_403_non_membership_reason(self, connector):
        """403 with other reason still returns True (break)."""
        from googleapiclient.errors import HttpError
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "forbidden"}]
        should_break = await connector._handle_drive_error(http_error, "Drive", "d1", "files")
        assert should_break is True

    async def test_500_http_error(self, connector):
        """500 HttpError returns True (break)."""
        from googleapiclient.errors import HttpError
        mock_resp = MagicMock()
        mock_resp.status = 500
        http_error = HttpError(mock_resp, b"server error")
        should_break = await connector._handle_drive_error(http_error, "Drive", "d1", "changes")
        assert should_break is True


# ---------------------------------------------------------------------------
# _process_drive_files_batch and _process_remaining_batch_records
# ---------------------------------------------------------------------------

class TestBatchProcessing:
    """Tests for batch processing methods."""

    async def test_process_remaining_batch_records_empty(self, connector):
        """Empty batch returns empty list."""
        result, count = await connector._process_remaining_batch_records([], "context")
        assert result == []
        assert count == 0

    async def test_process_remaining_batch_records_with_records(self, connector):
        """Non-empty batch is flushed."""
        batch = [("record1", ["perm1"])]
        result, count = await connector._process_remaining_batch_records(batch, "context")
        assert result == []
        assert count == 0
        connector.data_entities_processor.on_new_records.assert_called_once()


# ---------------------------------------------------------------------------
# _create_and_sync_shared_drive_record_group
# ---------------------------------------------------------------------------

class TestCreateSharedDriveRecordGroup:
    """Tests for _create_and_sync_shared_drive_record_group."""

    async def test_drive_without_id_skipped(self, connector):
        """Drive without ID is skipped."""
        await connector._create_and_sync_shared_drive_record_group({"name": "No ID"})
        connector.data_entities_processor.on_new_record_groups.assert_not_called()

    async def test_drive_with_bad_creation_time(self, connector):
        """Drive with bad creation time still processes."""
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [],
        })
        drive = {"id": "d1", "name": "TestDrive", "createdTime": "invalid"}
        await connector._create_and_sync_shared_drive_record_group(drive)
        connector.data_entities_processor.on_new_record_groups.assert_called_once()

    async def test_drive_error_raises(self, connector):
        """Error processing drive raises."""
        connector.drive_data_source.permissions_list = AsyncMock(
            side_effect=Exception("error")
        )
        drive = {"id": "d1", "name": "TestDrive"}
        with pytest.raises(Exception, match="error"):
            await connector._create_and_sync_shared_drive_record_group(drive)


# ---------------------------------------------------------------------------
# _sync_record_groups extended
# ---------------------------------------------------------------------------

class TestSyncRecordGroupsExtended:
    """Extended tests for _sync_record_groups."""

    async def test_shared_drive_error_continues(self, connector):
        """Error processing one shared drive continues to next."""
        connector.drive_data_source.drives_list = AsyncMock(return_value={
            "drives": [{"id": "d1", "name": "D1"}, {"id": "d2", "name": "D2"}],
        })
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=[
            Exception("error for d1"),
            {"permissions": []},
        ])
        connector.synced_users = []
        await connector._sync_record_groups()

    async def test_personal_drive_error_continues(self, connector):
        """Error creating personal drive continues to next user."""
        connector.drive_data_source.drives_list = AsyncMock(return_value={"drives": []})
        user1 = AppUser(
            app_name="DRIVE WORKSPACE", connector_id="drive-team-1",
            source_user_id="u1", email="u1@example.com", full_name="U1",
        )
        user2 = AppUser(
            app_name="DRIVE WORKSPACE", connector_id="drive-team-1",
            source_user_id="u2", email="u2@example.com", full_name="U2",
        )
        connector.synced_users = [user1, user2]
        with patch.object(connector, "_create_personal_record_group", new_callable=AsyncMock,
                         side_effect=[Exception("error"), None]):
            await connector._sync_record_groups()

    async def test_drives_list_pagination(self, connector):
        """Shared drives listing handles pagination."""
        connector.drive_data_source.drives_list = AsyncMock(side_effect=[
            {"drives": [{"id": "d1", "name": "D1"}], "nextPageToken": "p2"},
            {"drives": [{"id": "d2", "name": "D2"}]},
        ])
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        connector.synced_users = []
        await connector._sync_record_groups()
        assert connector.drive_data_source.drives_list.call_count == 2
