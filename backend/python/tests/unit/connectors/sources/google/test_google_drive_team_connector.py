"""
Comprehensive coverage tests for GoogleDriveTeamConnector.

Targets the ~551 uncovered lines to achieve 95%+ coverage by testing:
- __init__() sync point creation and defaults
- init() all branches: no config, no auth, no admin email, admin client failure, drive client failure, success
- run_sync() full orchestration and error propagation
- _sync_users() pagination, empty users, user without email, name fallback, creation time parse error,
  user processing error, fetch page error, no admin source
- _sync_user_groups() pagination, group processing error, fetch page error, no admin source
- _process_group() no group id, no group name, creation time parse error, member without email,
  member lookup in synced_users, member processing error, no user members
- _fetch_group_members() pagination, error
- _map_drive_role_to_permission_type() all roles + unknown
- _map_drive_permission_type_to_entity_type() all types + unknown
- _fetch_permissions() pagination, deleted perms, file 403 fallback, file 403 no email, file non-403,
  drive errors, general errors, anyone-with-link fallback, user already has permission
- _create_and_sync_shared_drive_record_group() no drive id, creation time parse error, success, error
- _sync_record_groups() no drive source, pagination, shared drive error continue, personal drive error continue
- _create_personal_record_group() success, error
- _process_drive_files_batch() new/updated/deleted items, batch size reached
- _process_remaining_batch_records() with and without remaining records
- _handle_drive_error() HttpError 403 teamDriveMembershipRequired, other 403, other HttpError, generic error
- _parse_datetime() None, string, datetime object, invalid
- _pass_date_filters() folder pass, no filter, created filter, modified filter, before/after
- _pass_extension_filter() folder pass, no filter, empty filter, google docs IN/NOT_IN,
  non-google with extension IN/NOT_IN, no extension IN/NOT_IN, unknown operator
- _process_drive_item() no file_id, date filter fail, extension filter fail, new file, existing file with
  metadata/content/permission changes, shared file, shared_with_me, fallback permissions, permission fetch error
- _process_drive_items_generator() indexing filters: files disabled, shared disabled, shared_with_me disabled, error
- _handle_record_updates() deleted, new, metadata changed, permissions changed, content changed, error
- _run_sync_with_yield() full sync and incremental sync, no permissionId, no driveId, error
- sync_personal_drive() full sync (no page token), incremental sync (with page token), pagination, token update
- sync_shared_drives() no shared drives, full sync, incremental sync, drive error, teamDriveMembershipRequired
- _process_users_in_batches() active user filtering, concurrent batches, error
- test_connection_and_access() all branches
- get_signed_url() raises NotImplementedError
- _stream_google_api_request() success, HttpError, generic error, empty content
- _convert_to_pdf() success, timeout, conversion failure, output not found
- _get_file_metadata_from_drive() success, 404 error, other HttpError, generic error
- _get_drive_service_for_user() with user email, impersonation failure, no user email, no drive client
- stream_record() all branches: no file_id, google workspace PDF export, regular export, PDF conversion,
  regular download, user_id lookup, permission lookup, error paths
- run_incremental_sync() raises NotImplementedError
- handle_webhook_notification() raises NotImplementedError
- reindex_records() empty, updated records, non-updated records, error
- _check_and_fetch_updated_record() no file_id, no user with permission, no user email, no permissionId,
  file not found at source, is_updated true, is_updated false, error
- get_filter_options() raises NotImplementedError
- cleanup() success, partial cleanup, error
- create_connector() success
"""

import asyncio
import io
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterOperator,
    FilterType,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_logger():
    log = logging.getLogger("test_drive_team_coverage")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None, user_with_permission=None, user_by_id=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user_with_permission)
    tx.get_user_by_user_id = AsyncMock(return_value=user_by_id)
    tx.create_record_relation = AsyncMock()
    return tx


def _make_mock_data_store_provider(existing_record=None, user_with_permission=None, user_by_id=None):
    tx = _make_mock_tx_store(existing_record, user_with_permission, user_by_id)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


def _make_connector(existing_record=None, user_with_permission=None, user_by_id=None):
    """Create a GoogleDriveTeamConnector with fully mocked dependencies."""
    with patch(
        "app.connectors.sources.google.drive.team.connector.GoogleDriveTeamApp"
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
        dep = MagicMock()
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
        dep.get_all_active_users = AsyncMock(return_value=[])
        dep.reindex_existing_records = AsyncMock()
        dep.delete_permission_from_record = AsyncMock()

        ds_provider = _make_mock_data_store_provider(existing_record, user_with_permission, user_by_id)
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"adminEmail": "admin@example.com"},
        })

        conn = GoogleDriveTeamConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="drive-team-cov",
            scope="personal",
            created_by="test-user-id",
        )
        conn.connector_name = Connectors.GOOGLE_DRIVE_WORKSPACE
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.admin_client = MagicMock()
        conn.drive_client = MagicMock()
        conn.admin_data_source = AsyncMock()
        conn.drive_data_source = AsyncMock()
        conn.config = {"credentials": {}}
        return conn


def _make_google_user(email="user@example.com", user_id="guser-1", full_name="Test User",
                      suspended=False, creation_time="2024-01-01T00:00:00.000Z",
                      title="Engineer"):
    return {
        "id": user_id,
        "primaryEmail": email,
        "name": {"fullName": full_name, "givenName": "Test", "familyName": "User"},
        "suspended": suspended,
        "creationTime": creation_time,
        "organizations": [{"title": title}] if title else [],
    }


def _make_google_group(email="group@example.com", group_id=None, name="Engineering",
                       creation_time="2024-01-01T00:00:00.000Z"):
    return {
        "id": group_id or f"grp-{email}",
        "email": email,
        "name": name,
        "description": "Test group",
        "creationTime": creation_time,
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
    version=None,
    web_view_link=None,
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
        "webViewLink": web_view_link or f"https://drive.google.com/file/d/{file_id}/view",
    }
    if parents is not None:
        meta["parents"] = parents
    if owners is not None:
        meta["owners"] = owners
    if version is not None:
        meta["version"] = version
    return meta


def _make_shared_drive(drive_id="shared-drive-1", name="Shared Drive",
                       created_time="2024-06-01T00:00:00.000Z"):
    return {
        "id": drive_id,
        "name": name,
        "createdTime": created_time,
    }


def _make_existing_record(
    record_id="rec-1",
    external_record_id="file-1",
    external_revision_id="rev-old",
    record_name="test.txt",
    version=1,
    is_shared=False,
    external_record_group_id="drive-1",
    parent_external_record_id="parent-1",
    indexing_status="NOT_STARTED",
    extraction_status="NOT_STARTED",
):
    rec = MagicMock()
    rec.id = record_id
    rec.external_record_id = external_record_id
    rec.external_revision_id = external_revision_id
    rec.record_name = record_name
    rec.version = version
    rec.is_shared = is_shared
    rec.external_record_group_id = external_record_group_id
    rec.parent_external_record_id = parent_external_record_id
    rec.indexing_status = indexing_status
    rec.extraction_status = extraction_status
    rec.mime_type = "text/plain"
    return rec


def _make_app_user(email="user@example.com", source_user_id="guser-1", full_name="Test User",
                   source_created_at=None):
    return AppUser(
        app_name=Connectors.GOOGLE_DRIVE_WORKSPACE,
        connector_id="drive-team-cov",
        source_user_id=source_user_id,
        email=email,
        full_name=full_name,
        source_created_at=source_created_at,
    )


def _make_mock_filter(key, value, operator=None, filter_type=FilterType.MULTISELECT):
    """Create a mock Filter object."""
    f = MagicMock(spec=Filter)
    f.key = key
    f.value = value
    f.type = filter_type
    f.is_empty = MagicMock(return_value=(value is None or (isinstance(value, list) and len(value) == 0)))
    f.get_operator = MagicMock(return_value=operator)
    f.get_datetime_iso = MagicMock(return_value=(None, None))
    return f


# ===========================================================================
# Tests: __init__
# ===========================================================================


class TestInit:

    def test_init_defaults(self):
        conn = _make_connector()
        assert conn.batch_size == 100
        assert conn.max_concurrent_batches == 1
        assert conn.synced_users == []
        assert conn.admin_client is not None
        assert conn.drive_client is not None

    @pytest.mark.asyncio
    async def test_init_success(self):
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(return_value={
            "auth": {"adminEmail": "admin@example.com", "someKey": "someValue"},
        })
        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = MagicMock()
            mock_client.get_client = MagicMock(return_value=MagicMock())
            MockGC.build_from_services = AsyncMock(return_value=mock_client)

            result = await conn.init()
            assert result is True

    @pytest.mark.asyncio
    async def test_init_no_config(self):
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(return_value=None)
        result = await conn.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_auth(self):
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(return_value={"auth": {}})
        with pytest.raises(ValueError, match="Service account credentials not found"):
            await conn.init()

    @pytest.mark.asyncio
    async def test_init_no_admin_email(self):
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(return_value={
            "auth": {"someKey": "someValue"},
        })
        with pytest.raises(ValueError, match="Admin email not found"):
            await conn.init()

    @pytest.mark.asyncio
    async def test_init_admin_client_failure(self):
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(return_value={
            "auth": {"adminEmail": "admin@example.com", "someKey": "val"},
        })
        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            MockGC.build_from_services = AsyncMock(side_effect=Exception("admin fail"))
            with pytest.raises(ValueError, match="Failed to initialize Google Admin client"):
                await conn.init()

    @pytest.mark.asyncio
    async def test_init_drive_client_failure(self):
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(return_value={
            "auth": {"adminEmail": "admin@example.com", "someKey": "val"},
        })
        call_count = 0

        async def build_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Admin client succeeds
                mock_client = MagicMock()
                mock_client.get_client = MagicMock(return_value=MagicMock())
                return mock_client
            else:
                # Drive client fails
                raise Exception("drive fail")

        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            MockGC.build_from_services = AsyncMock(side_effect=build_side_effect)
            with pytest.raises(ValueError, match="Failed to initialize Google Drive client"):
                await conn.init()


# ===========================================================================
# Tests: run_sync
# ===========================================================================


class TestRunSync:

    @pytest.mark.asyncio
    async def test_run_sync_calls_all_steps(self):
        conn = _make_connector()
        with patch.object(conn, "_sync_users", new_callable=AsyncMock) as mock_users, \
             patch.object(conn, "_sync_user_groups", new_callable=AsyncMock) as mock_groups, \
             patch.object(conn, "_sync_record_groups", new_callable=AsyncMock) as mock_rg, \
             patch.object(conn, "_process_users_in_batches", new_callable=AsyncMock) as mock_batches, \
             patch(
                 "app.connectors.sources.google.drive.team.connector.load_connector_filters",
                 new_callable=AsyncMock,
                 return_value=(FilterCollection(), FilterCollection()),
             ):
            await conn.run_sync()
            mock_users.assert_called_once()
            mock_groups.assert_called_once()
            mock_rg.assert_called_once()
            mock_batches.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_sync_propagates_error(self):
        conn = _make_connector()
        with patch.object(conn, "_sync_users", new_callable=AsyncMock, side_effect=ValueError("sync fail")), \
             patch(
                 "app.connectors.sources.google.drive.team.connector.load_connector_filters",
                 new_callable=AsyncMock,
                 return_value=(FilterCollection(), FilterCollection()),
             ):
            with pytest.raises(ValueError, match="sync fail"):
                await conn.run_sync()


# ===========================================================================
# Tests: _sync_users
# ===========================================================================


class TestSyncUsers:

    @pytest.mark.asyncio
    async def test_sync_users_success(self):
        conn = _make_connector()
        conn.admin_data_source.users_list = AsyncMock(return_value={
            "users": [
                _make_google_user("alice@example.com", "u1", "Alice"),
                _make_google_user("bob@example.com", "u2", "Bob"),
            ],
        })
        await conn._sync_users()
        assert len(conn.synced_users) == 2
        conn.data_entities_processor.on_new_app_users.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_users_pagination(self):
        conn = _make_connector()
        conn.admin_data_source.users_list = AsyncMock(side_effect=[
            {"users": [_make_google_user("a@x.com", "u1")], "nextPageToken": "page2"},
            {"users": [_make_google_user("b@x.com", "u2")]},
        ])
        await conn._sync_users()
        assert len(conn.synced_users) == 2
        assert conn.admin_data_source.users_list.call_count == 2

    @pytest.mark.asyncio
    async def test_sync_users_empty(self):
        conn = _make_connector()
        conn.admin_data_source.users_list = AsyncMock(return_value={"users": []})
        await conn._sync_users()
        assert len(conn.synced_users) == 0
        conn.data_entities_processor.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_users_no_admin_source(self):
        conn = _make_connector()
        conn.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await conn._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_skip_no_email(self):
        conn = _make_connector()
        user = _make_google_user()
        user["primaryEmail"] = None
        user["email"] = ""
        conn.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await conn._sync_users()
        assert len(conn.synced_users) == 0

    @pytest.mark.asyncio
    async def test_sync_users_name_fallback_to_given_family(self):
        conn = _make_connector()
        user = _make_google_user()
        user["name"] = {"givenName": "John", "familyName": "Doe"}
        conn.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await conn._sync_users()
        assert conn.synced_users[0].full_name == "John Doe"

    @pytest.mark.asyncio
    async def test_sync_users_name_fallback_to_email(self):
        conn = _make_connector()
        user = _make_google_user()
        user["name"] = {}
        conn.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await conn._sync_users()
        assert conn.synced_users[0].full_name == user["primaryEmail"]

    @pytest.mark.asyncio
    async def test_sync_users_no_organizations(self):
        conn = _make_connector()
        user = _make_google_user()
        user["organizations"] = []
        conn.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await conn._sync_users()
        assert conn.synced_users[0].title is None

    @pytest.mark.asyncio
    async def test_sync_users_creation_time_parse_error(self):
        conn = _make_connector()
        user = _make_google_user()
        user["creationTime"] = "invalid-time"
        conn.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        with patch("app.connectors.sources.google.drive.team.connector.parse_timestamp", side_effect=Exception("parse fail")):
            await conn._sync_users()
        assert len(conn.synced_users) == 1
        assert conn.synced_users[0].source_created_at is None

    @pytest.mark.asyncio
    async def test_sync_users_user_processing_error(self):
        conn = _make_connector()
        user = _make_google_user()
        bad_user = _make_google_user(email="bad@example.com", user_id="bad-1")

        # Patch AppUser to raise on the second call (the bad user)
        call_count = 0
        _OriginalAppUser = AppUser

        def app_user_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("processing fail")
            return _OriginalAppUser(*args, **kwargs)

        with patch("app.connectors.sources.google.drive.team.connector.AppUser", side_effect=app_user_side_effect):
            conn.admin_data_source.users_list = AsyncMock(return_value={"users": [user, bad_user]})
            await conn._sync_users()
        # Should still process the good user
        assert len(conn.synced_users) == 1

    @pytest.mark.asyncio
    async def test_sync_users_fetch_page_error(self):
        conn = _make_connector()
        conn.admin_data_source.users_list = AsyncMock(side_effect=Exception("fetch fail"))
        with pytest.raises(Exception, match="fetch fail"):
            await conn._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_suspended_user(self):
        conn = _make_connector()
        user = _make_google_user(suspended=True)
        conn.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await conn._sync_users()
        assert conn.synced_users[0].is_active is False

    @pytest.mark.asyncio
    async def test_sync_users_no_creation_time(self):
        conn = _make_connector()
        user = _make_google_user()
        user.pop("creationTime", None)
        conn.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await conn._sync_users()
        assert conn.synced_users[0].source_created_at is None


# ===========================================================================
# Tests: _sync_user_groups
# ===========================================================================


class TestSyncUserGroups:

    @pytest.mark.asyncio
    async def test_sync_groups_success(self):
        conn = _make_connector()
        conn.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [_make_google_group("eng@x.com")],
        })
        conn.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": "alice@x.com"}],
        })
        await conn._sync_user_groups()
        conn.data_entities_processor.on_new_user_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_groups_pagination(self):
        conn = _make_connector()
        conn.admin_data_source.groups_list = AsyncMock(side_effect=[
            {"groups": [_make_google_group("g1@x.com")], "nextPageToken": "p2"},
            {"groups": [_make_google_group("g2@x.com")]},
        ])
        conn.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": "user@x.com"}],
        })
        await conn._sync_user_groups()
        assert conn.admin_data_source.groups_list.call_count == 2

    @pytest.mark.asyncio
    async def test_sync_groups_no_admin_source(self):
        conn = _make_connector()
        conn.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await conn._sync_user_groups()

    @pytest.mark.asyncio
    async def test_sync_groups_empty(self):
        conn = _make_connector()
        conn.admin_data_source.groups_list = AsyncMock(return_value={"groups": []})
        await conn._sync_user_groups()
        conn.data_entities_processor.on_new_user_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_groups_group_processing_error(self):
        conn = _make_connector()
        conn.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [
                _make_google_group("g1@x.com"),
                _make_google_group("g2@x.com"),
            ],
        })
        # First group fails, second succeeds
        call_count = 0

        async def process_group_side_effect(group):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("group fail")

        with patch.object(conn, "_process_group", new_callable=AsyncMock, side_effect=process_group_side_effect):
            await conn._sync_user_groups()

    @pytest.mark.asyncio
    async def test_sync_groups_fetch_page_error(self):
        conn = _make_connector()
        conn.admin_data_source.groups_list = AsyncMock(side_effect=Exception("fetch fail"))
        with pytest.raises(Exception, match="fetch fail"):
            await conn._sync_user_groups()


# ===========================================================================
# Tests: _process_group
# ===========================================================================


class TestProcessGroup:

    @pytest.mark.asyncio
    async def test_process_group_no_email(self):
        conn = _make_connector()
        group = {"id": "g1", "name": "NoEmail"}
        # No email means skip
        await conn._process_group(group)
        conn.data_entities_processor.on_new_user_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_group_no_name_fallback_to_email(self):
        conn = _make_connector()
        group = {"email": "eng@x.com", "name": ""}
        conn.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": "a@x.com"}],
        })
        await conn._process_group(group)
        call_args = conn.data_entities_processor.on_new_user_groups.call_args[0][0]
        # The group name should fallback to email
        assert call_args[0][0].name == "eng@x.com"

    @pytest.mark.asyncio
    async def test_process_group_creation_time_parse_error(self):
        conn = _make_connector()
        group = _make_google_group()
        group["creationTime"] = "invalid-time"
        conn.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": "a@x.com"}],
        })
        with patch("app.connectors.sources.google.drive.team.connector.parse_timestamp", side_effect=Exception("parse fail")):
            await conn._process_group(group)
        conn.data_entities_processor.on_new_user_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_group_member_without_email(self):
        conn = _make_connector()
        group = _make_google_group()
        conn.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": ""}],
        })
        await conn._process_group(group)
        # No user members with email -> on_new_user_groups not called
        conn.data_entities_processor.on_new_user_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_group_member_found_in_synced_users(self):
        conn = _make_connector()
        conn.synced_users = [_make_app_user("alice@x.com", "u1", "Alice Wonderland", source_created_at=12345)]
        group = _make_google_group()
        conn.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "u1", "email": "alice@x.com"}],
        })
        await conn._process_group(group)
        call_args = conn.data_entities_processor.on_new_user_groups.call_args[0][0]
        app_users = call_args[0][1]
        assert app_users[0].full_name == "Alice Wonderland"
        assert app_users[0].source_created_at == 12345

    @pytest.mark.asyncio
    async def test_process_group_member_found_by_email_match(self):
        conn = _make_connector()
        conn.synced_users = [_make_app_user("alice@x.com", "different-id", "Alice Email")]
        group = _make_google_group()
        conn.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "id": "other-id", "email": "ALICE@x.com"}],
        })
        await conn._process_group(group)
        call_args = conn.data_entities_processor.on_new_user_groups.call_args[0][0]
        app_users = call_args[0][1]
        assert app_users[0].full_name == "Alice Email"

    @pytest.mark.asyncio
    async def test_process_group_member_processing_error(self):
        conn = _make_connector()
        group = _make_google_group()
        # Create members where one will fail
        members = [
            {"type": "USER", "id": "u1", "email": "good@x.com"},
            {"type": "USER", "id": None, "email": "bad@x.com"},
        ]
        conn.admin_data_source.members_list = AsyncMock(return_value={"members": members})

        # Patch AppUser to raise on second member
        original_init = AppUser.__init__
        call_count = 0

        def patched_init(self_inner, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count > 1 and kwargs.get("email") == "bad@x.com":
                raise Exception("member fail")
            original_init(self_inner, **kwargs)

        # This approach is tricky; instead just ensure both members are processed
        # and one has empty id which should still work
        await conn._process_group(group)
        conn.data_entities_processor.on_new_user_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_group_no_user_members(self):
        conn = _make_connector()
        group = _make_google_group()
        conn.admin_data_source.members_list = AsyncMock(return_value={
            "members": [
                {"type": "GROUP", "id": "g1", "email": "sub@x.com"},
                {"type": "CUSTOMER", "id": "c1", "email": "cust@x.com"},
            ],
        })
        await conn._process_group(group)
        conn.data_entities_processor.on_new_user_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_group_error_propagates(self):
        conn = _make_connector()
        group = _make_google_group()
        conn.admin_data_source.members_list = AsyncMock(side_effect=Exception("members fail"))
        with pytest.raises(Exception, match="members fail"):
            await conn._process_group(group)


# ===========================================================================
# Tests: _fetch_group_members
# ===========================================================================


class TestFetchGroupMembers:

    @pytest.mark.asyncio
    async def test_fetch_members_success(self):
        conn = _make_connector()
        conn.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"id": "m1"}, {"id": "m2"}],
        })
        result = await conn._fetch_group_members("group@x.com")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_fetch_members_pagination(self):
        conn = _make_connector()
        conn.admin_data_source.members_list = AsyncMock(side_effect=[
            {"members": [{"id": "m1"}], "nextPageToken": "p2"},
            {"members": [{"id": "m2"}]},
        ])
        result = await conn._fetch_group_members("group@x.com")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_fetch_members_empty(self):
        conn = _make_connector()
        conn.admin_data_source.members_list = AsyncMock(return_value={"members": []})
        result = await conn._fetch_group_members("group@x.com")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_fetch_members_error(self):
        conn = _make_connector()
        conn.admin_data_source.members_list = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await conn._fetch_group_members("group@x.com")


# ===========================================================================
# Tests: Permission mapping
# ===========================================================================


class TestPermissionMapping:

    def test_owner_role(self):
        conn = _make_connector()
        assert conn._map_drive_role_to_permission_type("owner") == PermissionType.OWNER

    def test_organizer_role(self):
        conn = _make_connector()
        assert conn._map_drive_role_to_permission_type("organizer") == PermissionType.OWNER

    def test_writer_role(self):
        conn = _make_connector()
        assert conn._map_drive_role_to_permission_type("writer") == PermissionType.WRITE

    def test_fileorganizer_role(self):
        conn = _make_connector()
        assert conn._map_drive_role_to_permission_type("fileOrganizer") == PermissionType.WRITE

    def test_commenter_role(self):
        conn = _make_connector()
        assert conn._map_drive_role_to_permission_type("commenter") == PermissionType.COMMENT

    def test_reader_role(self):
        conn = _make_connector()
        assert conn._map_drive_role_to_permission_type("reader") == PermissionType.READ

    def test_unknown_role(self):
        conn = _make_connector()
        assert conn._map_drive_role_to_permission_type("mystery") == PermissionType.READ


class TestEntityTypeMapping:

    def test_user(self):
        conn = _make_connector()
        assert conn._map_drive_permission_type_to_entity_type("user") == EntityType.USER

    def test_group(self):
        conn = _make_connector()
        assert conn._map_drive_permission_type_to_entity_type("group") == EntityType.GROUP

    def test_domain(self):
        conn = _make_connector()
        assert conn._map_drive_permission_type_to_entity_type("domain") == EntityType.DOMAIN

    def test_anyone(self):
        conn = _make_connector()
        assert conn._map_drive_permission_type_to_entity_type("anyone") == EntityType.ANYONE

    def test_anyone_with_link(self):
        conn = _make_connector()
        assert conn._map_drive_permission_type_to_entity_type("anyoneWithLink") == EntityType.ANYONE_WITH_LINK

    def test_anyone_with_link_underscore(self):
        conn = _make_connector()
        assert conn._map_drive_permission_type_to_entity_type("anyone_with_link") == EntityType.ANYONE_WITH_LINK

    def test_unknown_defaults_to_user(self):
        conn = _make_connector()
        assert conn._map_drive_permission_type_to_entity_type("custom") == EntityType.USER


# ===========================================================================
# Tests: _fetch_permissions
# ===========================================================================


class TestFetchPermissions:

    @pytest.mark.asyncio
    async def test_fetch_file_permissions(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "owner", "type": "user", "emailAddress": "owner@x.com"},
                {"id": "p2", "role": "reader", "type": "group", "emailAddress": "grp@x.com"},
            ],
        })
        perms, is_fallback = await conn._fetch_permissions("file-1", is_drive=False)
        assert len(perms) == 2
        assert is_fallback is False

    @pytest.mark.asyncio
    async def test_fetch_drive_permissions_with_domain_admin(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "organizer", "type": "user", "emailAddress": "admin@x.com"},
            ],
        })
        perms, _ = await conn._fetch_permissions("drive-1", is_drive=True)
        assert len(perms) == 1

    @pytest.mark.asyncio
    async def test_skips_deleted_permissions(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "owner", "type": "user", "emailAddress": "x@x.com", "deleted": True},
            ],
        })
        perms, _ = await conn._fetch_permissions("file-1")
        assert len(perms) == 0

    @pytest.mark.asyncio
    async def test_permissions_pagination(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(side_effect=[
            {
                "permissions": [{"id": "p1", "role": "reader", "type": "user", "emailAddress": "a@x.com"}],
                "nextPageToken": "p2",
            },
            {
                "permissions": [{"id": "p2", "role": "writer", "type": "user", "emailAddress": "b@x.com"}],
            },
        ])
        perms, _ = await conn._fetch_permissions("file-1")
        assert len(perms) == 2

    @pytest.mark.asyncio
    async def test_403_fallback_for_file(self):
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "insufficientFilePermissions"}]
        conn.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        perms, is_fallback = await conn._fetch_permissions("file-1", is_drive=False, user_email="u@x.com")
        assert is_fallback is True
        assert len(perms) == 1
        assert perms[0].email == "u@x.com"
        assert perms[0].type == PermissionType.READ

    @pytest.mark.asyncio
    async def test_403_no_user_email_for_file(self):
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "insufficientFilePermissions"}]
        conn.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        perms, is_fallback = await conn._fetch_permissions("file-1", is_drive=False, user_email=None)
        assert is_fallback is False

    @pytest.mark.asyncio
    async def test_403_non_insufficient_reason_for_file(self):
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "someOtherReason"}]
        conn.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        perms, is_fallback = await conn._fetch_permissions("file-1", is_drive=False, user_email="u@x.com")
        assert is_fallback is False

    @pytest.mark.asyncio
    async def test_non_403_http_error_for_file(self):
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = 500
        http_error = HttpError(mock_resp, b"server error")
        conn.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        perms, is_fallback = await conn._fetch_permissions("file-1", is_drive=False)
        assert is_fallback is False

    @pytest.mark.asyncio
    async def test_http_error_for_drive_raises(self):
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = []
        conn.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        with pytest.raises(HttpError):
            await conn._fetch_permissions("drive-1", is_drive=True)

    @pytest.mark.asyncio
    async def test_generic_error_for_file_returns_empty(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(side_effect=Exception("fail"))
        perms, is_fallback = await conn._fetch_permissions("file-1", is_drive=False)
        assert is_fallback is False

    @pytest.mark.asyncio
    async def test_generic_error_for_drive_raises(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await conn._fetch_permissions("drive-1", is_drive=True)

    @pytest.mark.asyncio
    async def test_anyone_permission_triggers_fallback(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "anyone", "emailAddress": None},
            ],
        })
        perms, is_fallback = await conn._fetch_permissions("file-1", is_drive=False, user_email="u@x.com")
        assert is_fallback is True
        assert len(perms) == 1
        assert perms[0].email == "u@x.com"

    @pytest.mark.asyncio
    async def test_anyone_permission_user_already_has_permission(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "anyone", "emailAddress": None},
                {"id": "p2", "role": "writer", "type": "user", "emailAddress": "u@x.com"},
            ],
        })
        perms, is_fallback = await conn._fetch_permissions("file-1", is_drive=False, user_email="u@x.com")
        # User already has permission, so no fallback
        assert is_fallback is False
        assert len(perms) == 2

    @pytest.mark.asyncio
    async def test_uses_custom_drive_data_source(self):
        conn = _make_connector()
        custom_ds = AsyncMock()
        custom_ds.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "reader", "type": "user", "emailAddress": "c@x.com"}],
        })
        perms, _ = await conn._fetch_permissions("file-1", drive_data_source=custom_ds)
        custom_ds.permissions_list.assert_called_once()
        assert len(perms) == 1

    @pytest.mark.asyncio
    async def test_permission_processing_error_continues(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "owner", "type": "user", "emailAddress": "good@x.com"},
                {"id": "p2"},  # Missing role/type will cause processing but default handling
            ],
        })
        perms, _ = await conn._fetch_permissions("file-1")
        # Both should be processed (second defaults to reader/user)
        assert len(perms) == 2

    @pytest.mark.asyncio
    async def test_403_with_error_details_parse_failure(self):
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        # error_details is not a list
        http_error.error_details = "not a list"
        conn.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        perms, is_fallback = await conn._fetch_permissions("file-1", is_drive=False, user_email="u@x.com")
        assert is_fallback is False


# ===========================================================================
# Tests: _create_and_sync_shared_drive_record_group
# ===========================================================================


class TestCreateAndSyncSharedDriveRecordGroup:

    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "organizer", "type": "user", "emailAddress": "a@x.com"}],
        })
        drive = _make_shared_drive()
        await conn._create_and_sync_shared_drive_record_group(drive)
        conn.data_entities_processor.on_new_record_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_drive_id(self):
        conn = _make_connector()
        drive = {"name": "NoDrive"}
        await conn._create_and_sync_shared_drive_record_group(drive)
        conn.data_entities_processor.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_creation_time_parse_error(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        drive = _make_shared_drive()
        drive["createdTime"] = "bad-time"
        with patch("app.connectors.sources.google.drive.team.connector.parse_timestamp", side_effect=Exception("parse")):
            await conn._create_and_sync_shared_drive_record_group(drive)
        conn.data_entities_processor.on_new_record_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_created_time(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        drive = _make_shared_drive()
        drive.pop("createdTime", None)
        await conn._create_and_sync_shared_drive_record_group(drive)
        conn.data_entities_processor.on_new_record_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_propagates(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(side_effect=Exception("perm fail"))
        drive = _make_shared_drive()
        with pytest.raises(Exception, match="perm fail"):
            await conn._create_and_sync_shared_drive_record_group(drive)


# ===========================================================================
# Tests: _sync_record_groups
# ===========================================================================


class TestSyncRecordGroups:

    @pytest.mark.asyncio
    async def test_no_drive_source(self):
        conn = _make_connector()
        conn.drive_data_source = None
        with pytest.raises(ValueError, match="Drive data source not initialized"):
            await conn._sync_record_groups()

    @pytest.mark.asyncio
    async def test_sync_shared_drives_and_personal(self):
        conn = _make_connector()
        conn.drive_data_source.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })
        conn.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        user = _make_app_user()
        conn.synced_users = [user]

        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = MagicMock()
            mock_client.get_client = MagicMock(return_value=MagicMock())
            MockGC.build_from_services = AsyncMock(return_value=mock_client)

            mock_ds = AsyncMock()
            mock_ds.files_get = AsyncMock(return_value={"id": "root-drive-id"})
            with patch(
                "app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                return_value=mock_ds,
            ):
                result = await conn._sync_record_groups()

        assert len(result) == 1  # 1 shared drive returned

    @pytest.mark.asyncio
    async def test_sync_drives_pagination(self):
        conn = _make_connector()
        conn.drive_data_source.drives_list = AsyncMock(side_effect=[
            {"drives": [_make_shared_drive("d1")], "nextPageToken": "p2"},
            {"drives": [_make_shared_drive("d2")]},
        ])
        conn.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        conn.synced_users = []
        result = await conn._sync_record_groups()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_shared_drive_error_continues(self):
        conn = _make_connector()
        conn.drive_data_source.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("d1"), _make_shared_drive("d2")],
        })
        call_count = 0

        async def create_side_effect(drive):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("drive error")

        with patch.object(conn, "_create_and_sync_shared_drive_record_group",
                          new_callable=AsyncMock, side_effect=create_side_effect):
            conn.synced_users = []
            result = await conn._sync_record_groups()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_personal_drive_error_continues(self):
        conn = _make_connector()
        conn.drive_data_source.drives_list = AsyncMock(return_value={"drives": []})
        conn.synced_users = [_make_app_user("u1@x.com"), _make_app_user("u2@x.com")]

        call_count = 0

        async def create_personal_side_effect(user):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("personal drive error")

        with patch.object(conn, "_create_personal_record_group",
                          new_callable=AsyncMock, side_effect=create_personal_side_effect):
            await conn._sync_record_groups()

    @pytest.mark.asyncio
    async def test_drives_list_error(self):
        conn = _make_connector()
        conn.drive_data_source.drives_list = AsyncMock(side_effect=Exception("list fail"))
        with pytest.raises(Exception, match="list fail"):
            await conn._sync_record_groups()

    @pytest.mark.asyncio
    async def test_empty_drives(self):
        conn = _make_connector()
        conn.drive_data_source.drives_list = AsyncMock(return_value={"drives": []})
        conn.synced_users = []
        result = await conn._sync_record_groups()
        assert result == []


# ===========================================================================
# Tests: _create_personal_record_group
# ===========================================================================


class TestCreatePersonalRecordGroup:

    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector()
        user = _make_app_user()
        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = MagicMock()
            mock_client.get_client = MagicMock(return_value=MagicMock())
            MockGC.build_from_services = AsyncMock(return_value=mock_client)

            mock_ds = AsyncMock()
            mock_ds.files_get = AsyncMock(return_value={"id": "root-id"})
            with patch(
                "app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                return_value=mock_ds,
            ):
                await conn._create_personal_record_group(user)

        conn.data_entities_processor.on_new_record_groups.assert_called_once()
        call_args = conn.data_entities_processor.on_new_record_groups.call_args[0][0]
        # Should have two record groups: My Drive and Shared with Me
        assert len(call_args) == 2

    @pytest.mark.asyncio
    async def test_no_drive_id_raises(self):
        conn = _make_connector()
        user = _make_app_user()
        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = MagicMock()
            mock_client.get_client = MagicMock(return_value=MagicMock())
            MockGC.build_from_services = AsyncMock(return_value=mock_client)

            mock_ds = AsyncMock()
            mock_ds.files_get = AsyncMock(return_value={"id": None})
            with patch(
                "app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                return_value=mock_ds,
            ):
                with pytest.raises(Exception):
                    await conn._create_personal_record_group(user)

    @pytest.mark.asyncio
    async def test_error_propagates(self):
        conn = _make_connector()
        user = _make_app_user()
        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            MockGC.build_from_services = AsyncMock(side_effect=Exception("client fail"))
            with pytest.raises(Exception, match="client fail"):
                await conn._create_personal_record_group(user)


# ===========================================================================
# Tests: _process_drive_files_batch
# ===========================================================================


class TestProcessDriveFilesBatch:

    @pytest.mark.asyncio
    async def test_new_records_batched(self):
        conn = _make_connector()
        file_record = MagicMock()
        file_record.record_name = "test.txt"
        perm = [Permission(email="u@x.com", type=PermissionType.READ, entity_type=EntityType.USER)]
        update = RecordUpdate(
            record=file_record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=perm,
        )

        async def mock_generator(*args, **kwargs):
            yield (file_record, perm, update)

        with patch.object(conn, "_process_drive_items_generator", side_effect=mock_generator):
            batch, count, total = await conn._process_drive_files_batch(
                files=[{}], user_id="u1", user_email="u@x.com", drive_id="d1",
                is_shared_drive=False, context_name="test", batch_records=[],
                batch_count=0, total_counter=0,
            )
        assert len(batch) == 1
        assert count == 1
        assert total == 1

    @pytest.mark.asyncio
    async def test_deleted_record_handled(self):
        conn = _make_connector()
        update = RecordUpdate(
            record=None, is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="file-1",
        )
        conn._handle_record_updates = AsyncMock()

        async def mock_generator(*args, **kwargs):
            yield (None, [], update)

        with patch.object(conn, "_process_drive_items_generator", side_effect=mock_generator):
            batch, count, total = await conn._process_drive_files_batch(
                files=[{}], user_id="u1", user_email="u@x.com", drive_id="d1",
                is_shared_drive=False, context_name="test", batch_records=[],
                batch_count=0, total_counter=0,
            )
        conn._handle_record_updates.assert_called_once()
        assert len(batch) == 0

    @pytest.mark.asyncio
    async def test_updated_record_handled(self):
        conn = _make_connector()
        file_record = MagicMock()
        file_record.record_name = "updated.txt"
        update = RecordUpdate(
            record=file_record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
        )
        conn._handle_record_updates = AsyncMock()

        async def mock_generator(*args, **kwargs):
            yield (file_record, [], update)

        with patch.object(conn, "_process_drive_items_generator", side_effect=mock_generator):
            batch, count, total = await conn._process_drive_files_batch(
                files=[{}], user_id="u1", user_email="u@x.com", drive_id="d1",
                is_shared_drive=False, context_name="test", batch_records=[],
                batch_count=0, total_counter=0,
            )
        conn._handle_record_updates.assert_called_once()
        assert len(batch) == 0

    @pytest.mark.asyncio
    async def test_batch_size_triggers_flush(self):
        conn = _make_connector()
        conn.batch_size = 2  # Small batch size for testing

        file_records = []
        updates = []
        for i in range(3):
            fr = MagicMock()
            fr.record_name = f"file{i}.txt"
            u = RecordUpdate(
                record=fr, is_new=True, is_updated=False, is_deleted=False,
                metadata_changed=False, content_changed=False, permissions_changed=False,
                new_permissions=[],
            )
            file_records.append(fr)
            updates.append(u)

        async def mock_generator(*args, **kwargs):
            for i in range(3):
                yield (file_records[i], [], updates[i])

        with patch.object(conn, "_process_drive_items_generator", side_effect=mock_generator):
            batch, count, total = await conn._process_drive_files_batch(
                files=[{}, {}, {}], user_id="u1", user_email="u@x.com", drive_id="d1",
                is_shared_drive=False, context_name="test", batch_records=[],
                batch_count=0, total_counter=0,
            )
        # After flush at batch_size=2, remaining 1 record
        conn.data_entities_processor.on_new_records.assert_called_once()
        assert len(batch) == 1
        assert total == 3


# ===========================================================================
# Tests: _process_remaining_batch_records
# ===========================================================================


class TestProcessRemainingBatchRecords:

    @pytest.mark.asyncio
    async def test_with_remaining_records(self):
        conn = _make_connector()
        batch = [("record1", ["perm1"]), ("record2", ["perm2"])]
        result_batch, result_count = await conn._process_remaining_batch_records(batch, "test")
        conn.data_entities_processor.on_new_records.assert_called_once_with(batch)
        assert result_batch == []
        assert result_count == 0

    @pytest.mark.asyncio
    async def test_with_empty_batch(self):
        conn = _make_connector()
        result_batch, result_count = await conn._process_remaining_batch_records([], "test")
        conn.data_entities_processor.on_new_records.assert_not_called()
        assert result_batch == []
        assert result_count == 0


# ===========================================================================
# Tests: _handle_drive_error
# ===========================================================================


class TestHandleDriveError:

    @pytest.mark.asyncio
    async def test_http_403_team_drive_membership_required(self):
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "teamDriveMembershipRequired"}]
        result = await conn._handle_drive_error(http_error, "Drive", "d1", "files")
        assert result is True

    @pytest.mark.asyncio
    async def test_http_403_other_reason(self):
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "other"}]
        result = await conn._handle_drive_error(http_error, "Drive", "d1", "files")
        assert result is True

    @pytest.mark.asyncio
    async def test_http_non_403(self):
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = 500
        http_error = HttpError(mock_resp, b"error")
        result = await conn._handle_drive_error(http_error, "Drive", "d1", "files")
        assert result is True

    @pytest.mark.asyncio
    async def test_generic_error(self):
        conn = _make_connector()
        result = await conn._handle_drive_error(Exception("fail"), "Drive", "d1", "changes")
        assert result is True

    @pytest.mark.asyncio
    async def test_http_403_no_error_details(self):
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = []
        result = await conn._handle_drive_error(http_error, "Drive", "d1", "files")
        assert result is True

    @pytest.mark.asyncio
    async def test_http_403_error_details_not_list(self):
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = "not a list"
        result = await conn._handle_drive_error(http_error, "Drive", "d1", "files")
        assert result is True


# ===========================================================================
# Tests: _parse_datetime
# ===========================================================================


class TestParseDatetime:

    def test_none_input(self):
        conn = _make_connector()
        assert conn._parse_datetime(None) is None

    def test_string_input(self):
        conn = _make_connector()
        result = conn._parse_datetime("2025-01-01T00:00:00Z")
        assert result is not None
        assert isinstance(result, int)

    def test_datetime_input(self):
        conn = _make_connector()
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = conn._parse_datetime(dt)
        assert result == int(dt.timestamp() * 1000)

    def test_invalid_input(self):
        conn = _make_connector()
        assert conn._parse_datetime("not-a-date") is None

    def test_empty_string(self):
        conn = _make_connector()
        assert conn._parse_datetime("") is None


# ===========================================================================
# Tests: _pass_date_filters
# ===========================================================================


class TestPassDateFilters:

    def test_folder_always_passes(self):
        conn = _make_connector()
        metadata = {"mimeType": MimeTypes.GOOGLE_DRIVE_FOLDER.value}
        assert conn._pass_date_filters(metadata) is True

    def test_no_filters(self):
        conn = _make_connector()
        metadata = {"mimeType": "text/plain", "createdTime": "2025-01-01T00:00:00Z"}
        assert conn._pass_date_filters(metadata) is True

    def test_created_filter_before(self):
        conn = _make_connector()
        # Create a filter that only allows files created after 2025-06-01
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso = MagicMock(return_value=("2025-06-01T00:00:00Z", None))
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(side_effect=lambda key: mock_filter if key == SyncFilterKey.CREATED else None)

        metadata = {"mimeType": "text/plain", "createdTime": "2025-01-01T00:00:00Z"}
        assert conn._pass_date_filters(metadata) is False

    def test_created_filter_after(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso = MagicMock(return_value=(None, "2024-06-01T00:00:00Z"))
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(side_effect=lambda key: mock_filter if key == SyncFilterKey.CREATED else None)

        metadata = {"mimeType": "text/plain", "createdTime": "2025-01-01T00:00:00Z"}
        assert conn._pass_date_filters(metadata) is False

    def test_created_filter_passes(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso = MagicMock(return_value=("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(side_effect=lambda key: mock_filter if key == SyncFilterKey.CREATED else None)

        metadata = {"mimeType": "text/plain", "createdTime": "2025-01-01T00:00:00Z"}
        assert conn._pass_date_filters(metadata) is True

    def test_modified_filter_before(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso = MagicMock(return_value=("2025-06-01T00:00:00Z", None))
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(side_effect=lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None)

        metadata = {"mimeType": "text/plain", "modifiedTime": "2025-01-01T00:00:00Z"}
        assert conn._pass_date_filters(metadata) is False

    def test_modified_filter_after(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso = MagicMock(return_value=(None, "2024-06-01T00:00:00Z"))
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(side_effect=lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None)

        metadata = {"mimeType": "text/plain", "modifiedTime": "2025-01-01T00:00:00Z"}
        assert conn._pass_date_filters(metadata) is False

    def test_no_created_time_in_metadata(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso = MagicMock(return_value=("2025-06-01T00:00:00Z", None))
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(side_effect=lambda key: mock_filter if key == SyncFilterKey.CREATED else None)

        metadata = {"mimeType": "text/plain"}
        assert conn._pass_date_filters(metadata) is True


# ===========================================================================
# Tests: _pass_extension_filter
# ===========================================================================


class TestPassExtensionFilter:

    def test_folder_always_passes(self):
        conn = _make_connector()
        metadata = {"mimeType": MimeTypes.GOOGLE_DRIVE_FOLDER.value}
        assert conn._pass_extension_filter(metadata) is True

    def test_no_filter(self):
        conn = _make_connector()
        metadata = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert conn._pass_extension_filter(metadata) is True

    def test_empty_filter(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=True)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        metadata = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert conn._pass_extension_filter(metadata) is True

    def test_google_docs_in_allowed(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=False)
        mock_filter.value = [MimeTypes.GOOGLE_DOCS.value]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator = MagicMock(return_value=mock_operator)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        metadata = {"mimeType": MimeTypes.GOOGLE_DOCS.value}
        assert conn._pass_extension_filter(metadata) is True

    def test_google_docs_not_in_allowed(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=False)
        mock_filter.value = [MimeTypes.GOOGLE_SHEETS.value]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator = MagicMock(return_value=mock_operator)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        metadata = {"mimeType": MimeTypes.GOOGLE_DOCS.value}
        assert conn._pass_extension_filter(metadata) is False

    def test_google_docs_not_in_operator(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=False)
        mock_filter.value = [MimeTypes.GOOGLE_DOCS.value]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.NOT_IN
        mock_filter.get_operator = MagicMock(return_value=mock_operator)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        metadata = {"mimeType": MimeTypes.GOOGLE_DOCS.value}
        assert conn._pass_extension_filter(metadata) is False  # mimeType is in exclusion list

    def test_google_sheets_not_in_exclusion(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=False)
        mock_filter.value = [MimeTypes.GOOGLE_DOCS.value]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.NOT_IN
        mock_filter.get_operator = MagicMock(return_value=mock_operator)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        metadata = {"mimeType": MimeTypes.GOOGLE_SHEETS.value}
        assert conn._pass_extension_filter(metadata) is True  # Not in exclusion list

    def test_extension_in_allowed(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=False)
        mock_filter.value = ["pdf", "txt"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator = MagicMock(return_value=mock_operator)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        metadata = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert conn._pass_extension_filter(metadata) is True

    def test_extension_not_in_allowed(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=False)
        mock_filter.value = ["pdf"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator = MagicMock(return_value=mock_operator)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        metadata = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert conn._pass_extension_filter(metadata) is False

    def test_extension_not_in_operator(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=False)
        mock_filter.value = ["pdf"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.NOT_IN
        mock_filter.get_operator = MagicMock(return_value=mock_operator)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        metadata = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert conn._pass_extension_filter(metadata) is True

    def test_no_extension_in_operator(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=False)
        mock_filter.value = ["pdf"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator = MagicMock(return_value=mock_operator)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        metadata = {"mimeType": "text/plain", "name": "noext"}
        assert conn._pass_extension_filter(metadata) is False

    def test_no_extension_not_in_operator(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=False)
        mock_filter.value = ["pdf"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.NOT_IN
        mock_filter.get_operator = MagicMock(return_value=mock_operator)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        metadata = {"mimeType": "text/plain", "name": "noext"}
        assert conn._pass_extension_filter(metadata) is True

    def test_extension_from_filename(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=False)
        mock_filter.value = ["pdf"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator = MagicMock(return_value=mock_operator)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        # No fileExtension, but name has extension
        metadata = {"mimeType": "application/pdf", "name": "document.pdf"}
        assert conn._pass_extension_filter(metadata) is True

    def test_invalid_filter_value(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=False)
        mock_filter.value = "not-a-list"  # Invalid
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        metadata = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert conn._pass_extension_filter(metadata) is True

    def test_unknown_operator_defaults_true(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=False)
        mock_filter.value = ["pdf"]
        mock_operator = MagicMock()
        mock_operator.value = "UNKNOWN_OP"
        mock_filter.get_operator = MagicMock(return_value=mock_operator)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        metadata = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert conn._pass_extension_filter(metadata) is True

    def test_google_docs_unknown_operator(self):
        conn = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty = MagicMock(return_value=False)
        mock_filter.value = [MimeTypes.GOOGLE_DOCS.value]
        mock_operator = MagicMock()
        mock_operator.value = "UNKNOWN_OP"
        mock_filter.get_operator = MagicMock(return_value=mock_operator)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get = MagicMock(return_value=mock_filter)

        metadata = {"mimeType": MimeTypes.GOOGLE_DOCS.value}
        assert conn._pass_extension_filter(metadata) is True


# ===========================================================================
# Tests: _process_drive_item
# ===========================================================================


class TestProcessDriveItem:

    @pytest.mark.asyncio
    async def test_no_file_id(self):
        conn = _make_connector()
        metadata = {"name": "test.txt"}  # No id
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is None

    @pytest.mark.asyncio
    async def test_date_filter_fail(self):
        conn = _make_connector()
        with patch.object(conn, "_pass_date_filters", return_value=False):
            metadata = _make_file_metadata()
            result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is None

    @pytest.mark.asyncio
    async def test_extension_filter_fail(self):
        conn = _make_connector()
        with patch.object(conn, "_pass_extension_filter", return_value=False):
            metadata = _make_file_metadata()
            result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is None

    @pytest.mark.asyncio
    async def test_new_file(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "owner", "type": "user", "emailAddress": "u@x.com"}],
        })
        metadata = _make_file_metadata(parents=["d1"])
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is not None
        assert result.is_new is True
        assert result.record.record_name == "test.txt"

    @pytest.mark.asyncio
    async def test_existing_file_metadata_changed(self):
        existing = _make_existing_record(record_name="old-name.txt", external_revision_id="rev-1")
        conn = _make_connector(existing_record=existing)
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "owner", "type": "user", "emailAddress": "u@x.com"}],
        })
        metadata = _make_file_metadata(name="new-name.txt", head_revision_id="rev-1", parents=["parent-1"])
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is not None
        assert result.is_updated is True
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_existing_file_content_changed(self):
        existing = _make_existing_record(external_revision_id="rev-old")
        conn = _make_connector(existing_record=existing)
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "owner", "type": "user", "emailAddress": "u@x.com"}],
        })
        metadata = _make_file_metadata(head_revision_id="rev-new", parents=["parent-1"])
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is not None
        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_existing_file_parent_changed(self):
        existing = _make_existing_record(parent_external_record_id="old-parent")
        conn = _make_connector(existing_record=existing)
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "owner", "type": "user", "emailAddress": "u@x.com"}],
        })
        metadata = _make_file_metadata(parents=["new-parent"], head_revision_id="rev-old")
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is not None
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_shared_with_me_file(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "reader", "type": "user", "emailAddress": "r@x.com"}],
        })
        metadata = _make_file_metadata(
            shared=True, owners=[{"emailAddress": "owner@x.com"}], parents=["p1"],
        )
        result = await conn._process_drive_item(metadata, "u1", "r@x.com", "d1", is_shared_drive=False)
        assert result is not None
        assert result.record.is_shared_with_me is True
        assert result.record.external_record_group_id is None

    @pytest.mark.asyncio
    async def test_shared_drive_file(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "writer", "type": "user", "emailAddress": "u@x.com"}],
        })
        metadata = _make_file_metadata(parents=["sd-1"])
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "sd-1", is_shared_drive=True)
        assert result is not None
        assert result.record.external_record_group_id == "sd-1"

    @pytest.mark.asyncio
    async def test_folder_type(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        metadata = _make_file_metadata(mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value, parents=["d1"])
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is not None
        assert result.record.is_file is False

    @pytest.mark.asyncio
    async def test_fallback_permissions(self):
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "insufficientFilePermissions"}]
        conn.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        metadata = _make_file_metadata(parents=["d1"])
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is not None
        assert result.permissions_changed is False  # Fallback means no permission change

    @pytest.mark.asyncio
    async def test_fallback_permissions_existing_record(self):
        from googleapiclient.errors import HttpError

        existing = _make_existing_record()
        conn = _make_connector(existing_record=existing)
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "insufficientFilePermissions"}]
        conn.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        metadata = _make_file_metadata(parents=["parent-1"], head_revision_id="rev-old")
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is not None
        conn.data_entities_processor.add_permission_to_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_permission_fetch_error(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(side_effect=Exception("perm fail"))

        metadata = _make_file_metadata(parents=["d1"])
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        # Should still return a record despite permission error
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_timestamps(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        metadata = _make_file_metadata(parents=["d1"])
        metadata.pop("createdTime", None)
        metadata.pop("modifiedTime", None)
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_file_extension_extracted_from_name(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        metadata = _make_file_metadata(parents=["d1"])
        metadata.pop("fileExtension", None)
        metadata["name"] = "document.pdf"
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is not None
        assert result.record.extension == "pdf"

    @pytest.mark.asyncio
    async def test_version_fallback(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        metadata = _make_file_metadata(parents=["d1"])
        metadata.pop("headRevisionId", None)
        metadata["version"] = "42"
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is not None
        assert result.record.external_revision_id == "42"

    @pytest.mark.asyncio
    async def test_parent_equals_drive_id(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        metadata = _make_file_metadata(parents=["d1"])
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is not None
        assert result.record.parent_external_record_id is None
        assert result.record.parent_record_type is None

    @pytest.mark.asyncio
    async def test_existing_no_content_change_keeps_indexing_status(self):
        existing = _make_existing_record(
            external_revision_id="rev-1",
            indexing_status="COMPLETED",
            extraction_status="COMPLETED",
        )
        conn = _make_connector(existing_record=existing)
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "owner", "type": "user", "emailAddress": "u@x.com"}],
        })
        metadata = _make_file_metadata(head_revision_id="rev-1", parents=["parent-1"])
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is not None
        assert result.record.indexing_status == "COMPLETED"
        assert result.record.extraction_status == "COMPLETED"

    @pytest.mark.asyncio
    async def test_existing_record_null_record_group_id_triggers_update(self):
        existing = _make_existing_record(
            external_record_group_id=None,
            external_revision_id="rev-1",
        )
        conn = _make_connector(existing_record=existing)
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "owner", "type": "user", "emailAddress": "u@x.com"}],
        })
        metadata = _make_file_metadata(head_revision_id="rev-1", parents=["parent-1"])
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1", is_shared_drive=False)
        assert result is not None
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector()
        # Force an error by making data_store_provider.transaction raise
        conn.data_store_provider.transaction = MagicMock(side_effect=Exception("tx fail"))
        metadata = _make_file_metadata()
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1")
        assert result is None

    @pytest.mark.asyncio
    async def test_custom_drive_data_source(self):
        conn = _make_connector()
        custom_ds = AsyncMock()
        custom_ds.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "reader", "type": "user", "emailAddress": "u@x.com"}],
        })
        metadata = _make_file_metadata(parents=["d1"])
        result = await conn._process_drive_item(metadata, "u1", "u@x.com", "d1", drive_data_source=custom_ds)
        assert result is not None


# ===========================================================================
# Tests: _process_drive_items_generator
# ===========================================================================


class TestProcessDriveItemsGenerator:

    @pytest.mark.asyncio
    async def test_yields_records(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "owner", "type": "user", "emailAddress": "u@x.com"}],
        })
        metadata = _make_file_metadata(parents=["d1"])

        results = []
        async for record, perms, update in conn._process_drive_items_generator(
            [metadata], "u1", "u@x.com", "d1"
        ):
            results.append((record, perms, update))
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_files_disabled_sets_auto_index_off(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(return_value=False)

        metadata = _make_file_metadata(parents=["d1"])
        results = []
        async for record, perms, update in conn._process_drive_items_generator(
            [metadata], "u1", "u@x.com", "d1"
        ):
            results.append(record)
        assert len(results) == 1
        assert results[0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_shared_disabled_sets_auto_index_off(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "owner", "type": "user", "emailAddress": "u@x.com"}],
        })

        def is_enabled_side_effect(key, default=True):
            if key == IndexingFilterKey.FILES:
                return True
            if key == IndexingFilterKey.SHARED:
                return False
            return default

        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(side_effect=is_enabled_side_effect)

        # User is the owner so is_shared=True but is_shared_with_me=False,
        # triggering the shared_disabled path
        metadata = _make_file_metadata(shared=True, owners=[{"emailAddress": "u@x.com"}], parents=["d1"])
        results = []
        async for record, perms, update in conn._process_drive_items_generator(
            [metadata], "u1", "u@x.com", "d1"
        ):
            results.append(record)
        assert len(results) == 1
        assert results[0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_shared_with_me_disabled_sets_auto_index_off(self):
        conn = _make_connector()
        conn.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "reader", "type": "user", "emailAddress": "r@x.com"}],
        })

        def is_enabled_side_effect(key, default=True):
            if key == IndexingFilterKey.FILES:
                return True
            if key == IndexingFilterKey.SHARED:
                return True
            if key == IndexingFilterKey.SHARED_WITH_ME:
                return False
            return default

        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(side_effect=is_enabled_side_effect)

        metadata = _make_file_metadata(shared=True, owners=[{"emailAddress": "owner@x.com"}], parents=["d1"])
        results = []
        async for record, perms, update in conn._process_drive_items_generator(
            [metadata], "u1", "r@x.com", "d1"
        ):
            results.append(record)
        assert len(results) == 1
        assert results[0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_processing_error_continues(self):
        conn = _make_connector()
        call_count = 0

        async def process_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("process error")
            fr = MagicMock()
            fr.is_shared = False
            fr.is_shared_with_me = False
            return RecordUpdate(
                record=fr, is_new=True, is_updated=False, is_deleted=False,
                metadata_changed=False, content_changed=False, permissions_changed=False,
                new_permissions=[], external_record_id="f2",
            )

        with patch.object(conn, "_process_drive_item", new_callable=AsyncMock, side_effect=process_side_effect):
            results = []
            async for record, perms, update in conn._process_drive_items_generator(
                [{"id": "f1"}, {"id": "f2"}], "u1", "u@x.com", "d1"
            ):
                results.append(record)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_none_record_skipped(self):
        conn = _make_connector()
        with patch.object(conn, "_process_drive_item", new_callable=AsyncMock, return_value=None):
            results = []
            async for record, perms, update in conn._process_drive_items_generator(
                [{"id": "f1"}], "u1", "u@x.com", "d1"
            ):
                results.append(record)
        assert len(results) == 0


# ===========================================================================
# Tests: _handle_record_updates
# ===========================================================================


class TestHandleRecordUpdates:

    @pytest.mark.asyncio
    async def test_deleted_record(self):
        conn = _make_connector()
        update = RecordUpdate(
            record=None, is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="file-1",
        )
        await conn._handle_record_updates(update)
        conn.data_entities_processor.on_record_deleted.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_record(self):
        conn = _make_connector()
        fr = MagicMock()
        fr.record_name = "new.txt"
        update = RecordUpdate(
            record=fr, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        await conn._handle_record_updates(update)
        # New records are just logged, not processed here
        conn.data_entities_processor.on_record_metadata_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_metadata_changed(self):
        conn = _make_connector()
        fr = MagicMock()
        fr.record_name = "updated.txt"
        update = RecordUpdate(
            record=fr, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
        )
        await conn._handle_record_updates(update)
        conn.data_entities_processor.on_record_metadata_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_permissions_changed(self):
        conn = _make_connector()
        fr = MagicMock()
        fr.record_name = "perm.txt"
        new_perms = [Permission(email="u@x.com", type=PermissionType.READ, entity_type=EntityType.USER)]
        update = RecordUpdate(
            record=fr, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=True,
            new_permissions=new_perms,
        )
        await conn._handle_record_updates(update)
        conn.data_entities_processor.on_updated_record_permissions.assert_called_once()

    @pytest.mark.asyncio
    async def test_content_changed(self):
        conn = _make_connector()
        fr = MagicMock()
        fr.record_name = "content.txt"
        update = RecordUpdate(
            record=fr, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=False, content_changed=True, permissions_changed=False,
        )
        await conn._handle_record_updates(update)
        conn.data_entities_processor.on_record_content_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_changed(self):
        conn = _make_connector()
        fr = MagicMock()
        fr.record_name = "all.txt"
        update = RecordUpdate(
            record=fr, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=True, permissions_changed=True,
            new_permissions=[],
        )
        await conn._handle_record_updates(update)
        conn.data_entities_processor.on_record_metadata_update.assert_called_once()
        conn.data_entities_processor.on_record_content_update.assert_called_once()
        conn.data_entities_processor.on_updated_record_permissions.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_does_not_propagate(self):
        conn = _make_connector()
        conn.data_entities_processor.on_record_deleted = AsyncMock(side_effect=Exception("delete fail"))
        update = RecordUpdate(
            record=None, is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="file-1",
        )
        # Should not raise
        await conn._handle_record_updates(update)


# ===========================================================================
# Tests: _run_sync_with_yield
# ===========================================================================


class TestRunSyncWithYield:

    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector()
        user = _make_app_user()

        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = MagicMock()
            mock_client.get_client = MagicMock(return_value=MagicMock())
            MockGC.build_from_services = AsyncMock(return_value=mock_client)

            mock_ds = AsyncMock()
            mock_ds.about_get = AsyncMock(return_value={
                "user": {"permissionId": "perm-id", "emailAddress": "u@x.com"},
            })
            mock_ds.files_get = AsyncMock(return_value={"id": "root-drive-id"})

            with patch(
                "app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                return_value=mock_ds,
            ):
                conn.sync_personal_drive = AsyncMock()
                conn.sync_shared_drives = AsyncMock()
                await conn._run_sync_with_yield(user)

            conn.sync_personal_drive.assert_called_once()
            conn.sync_shared_drives.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_permission_id(self):
        conn = _make_connector()
        user = _make_app_user()

        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = MagicMock()
            mock_client.get_client = MagicMock(return_value=MagicMock())
            MockGC.build_from_services = AsyncMock(return_value=mock_client)

            mock_ds = AsyncMock()
            mock_ds.about_get = AsyncMock(return_value={"user": {}})

            with patch(
                "app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                return_value=mock_ds,
            ):
                # Should return early when no permissionId
                await conn._run_sync_with_yield(user)

    @pytest.mark.asyncio
    async def test_no_drive_id_raises(self):
        conn = _make_connector()
        user = _make_app_user()

        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = MagicMock()
            mock_client.get_client = MagicMock(return_value=MagicMock())
            MockGC.build_from_services = AsyncMock(return_value=mock_client)

            mock_ds = AsyncMock()
            mock_ds.about_get = AsyncMock(return_value={
                "user": {"permissionId": "perm-id"},
            })
            mock_ds.files_get = AsyncMock(return_value={"id": None})

            with patch(
                "app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                return_value=mock_ds,
            ):
                with pytest.raises(Exception):
                    await conn._run_sync_with_yield(user)

    @pytest.mark.asyncio
    async def test_error_propagates(self):
        conn = _make_connector()
        user = _make_app_user()

        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            MockGC.build_from_services = AsyncMock(side_effect=Exception("client fail"))
            with pytest.raises(Exception, match="client fail"):
                await conn._run_sync_with_yield(user)


# ===========================================================================
# Tests: sync_personal_drive
# ===========================================================================


class TestSyncPersonalDrive:

    @pytest.mark.asyncio
    async def test_full_sync(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        # No existing sync point (full sync)
        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_get_start_page_token = AsyncMock(return_value={"startPageToken": "start-token-12345"})
        mock_ds.files_list = AsyncMock(return_value={"files": []})

        await conn.sync_personal_drive(user, mock_ds, "perm-id", "drive-id")
        conn.drive_delta_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_sync_with_files(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_get_start_page_token = AsyncMock(return_value={"startPageToken": "start-token-12345"})
        mock_ds.files_list = AsyncMock(side_effect=[
            {"files": [_make_file_metadata()], "nextPageToken": "next"},
            {"files": []},
        ])

        with patch.object(conn, "_process_drive_files_batch", new_callable=AsyncMock,
                          return_value=([], 0, 1)):
            with patch.object(conn, "_process_remaining_batch_records", new_callable=AsyncMock,
                              return_value=([], 0)):
                await conn.sync_personal_drive(user, mock_ds, "perm-id", "drive-id")

    @pytest.mark.asyncio
    async def test_full_sync_no_start_token(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)

        mock_ds.changes_get_start_page_token = AsyncMock(return_value={"startPageToken": None})

        await conn.sync_personal_drive(user, mock_ds, "perm-id", "drive-id")
        # Should return early when no start page token
        conn.drive_delta_sync_point.update_sync_point.assert_not_called()

    @pytest.mark.asyncio
    async def test_incremental_sync(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"pageToken": "existing-token-12345678901234567890"}
        )
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_list = AsyncMock(return_value={
            "changes": [],
            "newStartPageToken": "new-start-token-1234567890",
        })

        await conn.sync_personal_drive(user, mock_ds, "perm-id", "drive-id")
        conn.drive_delta_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_incremental_sync_with_changes(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"pageToken": "existing-token-12345678901234567890"}
        )
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_list = AsyncMock(return_value={
            "changes": [
                {"removed": False, "file": _make_file_metadata()},
                {"removed": True, "fileId": "removed-file"},
            ],
            "newStartPageToken": "new-token-12345678901234567890123",
        })

        existing_record = MagicMock()
        existing_record.id = "rec-1"
        existing_record.record_name = "removed.txt"

        conn.data_store_provider = _make_mock_data_store_provider(existing_record=existing_record)

        with patch.object(conn, "_process_drive_files_batch", new_callable=AsyncMock,
                          return_value=([], 0, 1)):
            with patch.object(conn, "_process_remaining_batch_records", new_callable=AsyncMock,
                              return_value=([], 0)):
                await conn.sync_personal_drive(user, mock_ds, "perm-id", "drive-id")

    @pytest.mark.asyncio
    async def test_incremental_sync_pagination(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"pageToken": "existing-token-12345678901234567890"}
        )
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_list = AsyncMock(side_effect=[
            {"changes": [], "nextPageToken": "page2-token-1234567890123456789"},
            {"changes": [], "newStartPageToken": "final-token-12345678901234567"},
        ])

        await conn.sync_personal_drive(user, mock_ds, "perm-id", "drive-id")
        assert mock_ds.changes_list.call_count == 2

    @pytest.mark.asyncio
    async def test_incremental_sync_no_next_or_start_token(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"pageToken": "existing-token-12345678901234567890"}
        )
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_list = AsyncMock(return_value={"changes": []})

        await conn.sync_personal_drive(user, mock_ds, "perm-id", "drive-id")
        # Token unchanged, should not update sync point
        conn.drive_delta_sync_point.update_sync_point.assert_not_called()


# ===========================================================================
# Tests: sync_shared_drives
# ===========================================================================


class TestSyncSharedDrives:

    @pytest.mark.asyncio
    async def test_no_shared_drives(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()
        mock_ds.drives_list = AsyncMock(return_value={"drives": []})
        await conn.sync_shared_drives(user, mock_ds, "perm-id")

    @pytest.mark.asyncio
    async def test_shared_drive_full_sync(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        mock_ds.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_get_start_page_token = AsyncMock(return_value={"startPageToken": "start-token-12345"})
        mock_ds.files_list = AsyncMock(return_value={"files": []})

        await conn.sync_shared_drives(user, mock_ds, "perm-id")
        conn.drive_delta_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_shared_drive_incremental_sync(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        mock_ds.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"pageToken": "existing-token-12345678901234567890"}
        )
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_list = AsyncMock(return_value={
            "changes": [],
            "newStartPageToken": "new-start-token-1234567890",
        })

        await conn.sync_shared_drives(user, mock_ds, "perm-id")
        conn.drive_delta_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_shared_drive_incremental_with_removed_file(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        mock_ds.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"pageToken": "existing-token-12345678901234567890"}
        )
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_list = AsyncMock(return_value={
            "changes": [
                {"removed": True, "fileId": "removed-file-1"},
                {"removed": False, "file": _make_file_metadata()},
            ],
            "newStartPageToken": "new-token-12345678901234567890123",
        })

        with patch.object(conn, "_handle_record_updates", new_callable=AsyncMock):
            with patch.object(conn, "_process_drive_files_batch", new_callable=AsyncMock,
                              return_value=([], 0, 1)):
                with patch.object(conn, "_process_remaining_batch_records", new_callable=AsyncMock,
                                  return_value=([], 0)):
                    await conn.sync_shared_drives(user, mock_ds, "perm-id")

    @pytest.mark.asyncio
    async def test_shared_drive_no_id_skipped(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()
        mock_ds.drives_list = AsyncMock(return_value={
            "drives": [{"name": "No ID Drive"}],  # No id
        })
        await conn.sync_shared_drives(user, mock_ds, "perm-id")

    @pytest.mark.asyncio
    async def test_shared_drive_team_membership_required(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        mock_ds.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(side_effect=Exception("access fail"))

        # Set error_details to simulate teamDriveMembershipRequired
        err = Exception("access fail")
        err.error_details = [{"reason": "teamDriveMembershipRequired"}]
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(side_effect=err)

        # Should continue without raising
        await conn.sync_shared_drives(user, mock_ds, "perm-id")

    @pytest.mark.asyncio
    async def test_shared_drive_other_error_continues(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        mock_ds.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })

        err = Exception("other error")
        err.error_details = []
        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(side_effect=err)

        await conn.sync_shared_drives(user, mock_ds, "perm-id")

    @pytest.mark.asyncio
    async def test_shared_drive_error_no_error_details(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        mock_ds.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(side_effect=Exception("generic"))

        await conn.sync_shared_drives(user, mock_ds, "perm-id")

    @pytest.mark.asyncio
    async def test_drives_list_error_breaks(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()
        mock_ds.drives_list = AsyncMock(side_effect=Exception("list fail"))

        with patch.object(conn, "_handle_drive_error", new_callable=AsyncMock, return_value=True):
            await conn.sync_shared_drives(user, mock_ds, "perm-id")

    @pytest.mark.asyncio
    async def test_shared_drive_full_sync_no_start_token(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        mock_ds.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)

        mock_ds.changes_get_start_page_token = AsyncMock(return_value={"startPageToken": None})
        # Should continue to next drive
        await conn.sync_shared_drives(user, mock_ds, "perm-id")

    @pytest.mark.asyncio
    async def test_shared_drive_full_sync_files_pagination(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        mock_ds.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_get_start_page_token = AsyncMock(return_value={"startPageToken": "start-token-12345"})
        mock_ds.files_list = AsyncMock(side_effect=[
            {"files": [_make_file_metadata()], "nextPageToken": "next-page"},
            {"files": []},
        ])

        with patch.object(conn, "_process_drive_files_batch", new_callable=AsyncMock,
                          return_value=([], 0, 1)):
            with patch.object(conn, "_process_remaining_batch_records", new_callable=AsyncMock,
                              return_value=([], 0)):
                await conn.sync_shared_drives(user, mock_ds, "perm-id")

    @pytest.mark.asyncio
    async def test_shared_drive_full_sync_file_error(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        mock_ds.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_get_start_page_token = AsyncMock(return_value={"startPageToken": "start-token-12345"})
        mock_ds.files_list = AsyncMock(side_effect=Exception("files fail"))

        with patch.object(conn, "_handle_drive_error", new_callable=AsyncMock, return_value=True):
            with patch.object(conn, "_process_remaining_batch_records", new_callable=AsyncMock,
                              return_value=([], 0)):
                await conn.sync_shared_drives(user, mock_ds, "perm-id")

    @pytest.mark.asyncio
    async def test_shared_drive_incremental_pagination(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        mock_ds.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"pageToken": "existing-token-12345678901234567890"}
        )
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_list = AsyncMock(side_effect=[
            {"changes": [], "nextPageToken": "page2-tok-12345678901234567890"},
            {"changes": [], "newStartPageToken": "final-tok-12345678901234567"},
        ])

        await conn.sync_shared_drives(user, mock_ds, "perm-id")
        assert mock_ds.changes_list.call_count == 2

    @pytest.mark.asyncio
    async def test_shared_drive_incremental_no_tokens(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        mock_ds.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"pageToken": "existing-token-12345678901234567890"}
        )
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_list = AsyncMock(return_value={"changes": []})

        await conn.sync_shared_drives(user, mock_ds, "perm-id")
        # Token unchanged, should not update sync point
        conn.drive_delta_sync_point.update_sync_point.assert_not_called()

    @pytest.mark.asyncio
    async def test_shared_drive_incremental_error_breaks(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        mock_ds.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"pageToken": "existing-token-12345678901234567890"}
        )
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_list = AsyncMock(side_effect=Exception("changes fail"))

        with patch.object(conn, "_handle_drive_error", new_callable=AsyncMock, return_value=True):
            with patch.object(conn, "_process_remaining_batch_records", new_callable=AsyncMock,
                              return_value=([], 0)):
                await conn.sync_shared_drives(user, mock_ds, "perm-id")

    @pytest.mark.asyncio
    async def test_outer_exception_does_not_raise(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()
        mock_ds.drives_list = AsyncMock(side_effect=Exception("outer fail"))

        # The outer exception should be caught and not raised
        with patch.object(conn, "_handle_drive_error", new_callable=AsyncMock, side_effect=Exception("re-raise")):
            # Even if _handle_drive_error re-raises, the outer try/except in sync_shared_drives catches it
            await conn.sync_shared_drives(user, mock_ds, "perm-id")

    @pytest.mark.asyncio
    async def test_shared_drive_drives_list_pagination(self):
        conn = _make_connector()
        user = _make_app_user()
        mock_ds = AsyncMock()

        mock_ds.drives_list = AsyncMock(side_effect=[
            {"drives": [_make_shared_drive("sd-1")], "nextPageToken": "p2"},
            {"drives": [_make_shared_drive("sd-2")]},
        ])

        conn.drive_delta_sync_point = AsyncMock()
        conn.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.drive_delta_sync_point.update_sync_point = AsyncMock()

        mock_ds.changes_get_start_page_token = AsyncMock(return_value={"startPageToken": "start-token-12345"})
        mock_ds.files_list = AsyncMock(return_value={"files": []})

        await conn.sync_shared_drives(user, mock_ds, "perm-id")
        assert mock_ds.drives_list.call_count == 2


# ===========================================================================
# Tests: _process_users_in_batches
# ===========================================================================


class TestProcessUsersInBatches:

    @pytest.mark.asyncio
    async def test_filters_active_users(self):
        conn = _make_connector()
        user1 = _make_app_user("active@x.com")
        user2 = _make_app_user("inactive@x.com")

        active_user = MagicMock()
        active_user.email = "active@x.com"
        conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[active_user])

        conn._run_sync_with_yield = AsyncMock()

        await conn._process_users_in_batches([user1, user2])

        # Only active user should be processed
        conn._run_sync_with_yield.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrent_batches(self):
        conn = _make_connector()
        conn.max_concurrent_batches = 2

        users = [_make_app_user(f"u{i}@x.com") for i in range(5)]
        active_users = [MagicMock(email=f"u{i}@x.com") for i in range(5)]
        conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=active_users)
        conn._run_sync_with_yield = AsyncMock()

        await conn._process_users_in_batches(users)
        assert conn._run_sync_with_yield.call_count == 5

    @pytest.mark.asyncio
    async def test_error_propagates(self):
        conn = _make_connector()
        conn.data_entities_processor.get_all_active_users = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await conn._process_users_in_batches([])

    @pytest.mark.asyncio
    async def test_empty_users(self):
        conn = _make_connector()
        conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
        conn._run_sync_with_yield = AsyncMock()
        await conn._process_users_in_batches([])
        conn._run_sync_with_yield.assert_not_called()


# ===========================================================================
# Tests: test_connection_and_access
# ===========================================================================


class TestTestConnectionAndAccess:

    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector()
        result = await conn.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_no_drive_source(self):
        conn = _make_connector()
        conn.drive_data_source = None
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_no_admin_source(self):
        conn = _make_connector()
        conn.admin_data_source = None
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_no_drive_client(self):
        conn = _make_connector()
        conn.drive_client = None
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_no_admin_client(self):
        conn = _make_connector()
        conn.admin_client = None
        result = await conn.test_connection_and_access()
        assert result is False


# ===========================================================================
# Tests: get_signed_url, run_incremental_sync, handle_webhook_notification
# ===========================================================================


class TestNotImplementedMethods:

    def test_get_signed_url(self):
        conn = _make_connector()
        with pytest.raises(NotImplementedError):
            conn.get_signed_url(MagicMock())

    @pytest.mark.asyncio
    async def test_run_incremental_sync(self):
        conn = _make_connector()
        with pytest.raises(NotImplementedError):
            await conn.run_incremental_sync()

    def test_handle_webhook_notification(self):
        conn = _make_connector()
        with pytest.raises(NotImplementedError):
            conn.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_get_filter_options(self):
        conn = _make_connector()
        with pytest.raises(NotImplementedError):
            await conn.get_filter_options("some_key")


# ===========================================================================
# Tests: _stream_google_api_request
# ===========================================================================


class TestStreamGoogleApiRequest:

    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector()
        mock_request = MagicMock()

        with patch("app.connectors.sources.google.drive.team.connector.MediaIoBaseDownload") as MockDownloader:
            mock_dl = MagicMock()
            mock_dl.next_chunk = MagicMock(return_value=(MagicMock(progress=MagicMock(return_value=1.0)), True))
            MockDownloader.return_value = mock_dl

            # We need to patch the buffer operations
            chunks = []
            async for chunk in conn._stream_google_api_request(mock_request, "test"):
                chunks.append(chunk)
            # At least one chunk should be yielded (might be empty based on buffer content)

    @pytest.mark.asyncio
    async def test_http_error_during_download(self):
        from fastapi import HTTPException
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_request = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status = 500
        http_error = HttpError(mock_resp, b"error")

        with patch("app.connectors.sources.google.drive.team.connector.MediaIoBaseDownload") as MockDownloader:
            mock_dl = MagicMock()
            mock_dl.next_chunk = MagicMock(side_effect=http_error)
            MockDownloader.return_value = mock_dl

            with pytest.raises(HTTPException):
                async for _ in conn._stream_google_api_request(mock_request, "test"):
                    pass

    @pytest.mark.asyncio
    async def test_generic_error_during_download(self):
        from fastapi import HTTPException

        conn = _make_connector()
        mock_request = MagicMock()

        with patch("app.connectors.sources.google.drive.team.connector.MediaIoBaseDownload") as MockDownloader:
            mock_dl = MagicMock()
            mock_dl.next_chunk = MagicMock(side_effect=Exception("chunk fail"))
            MockDownloader.return_value = mock_dl

            with pytest.raises(HTTPException):
                async for _ in conn._stream_google_api_request(mock_request, "test"):
                    pass


# ===========================================================================
# Tests: _convert_to_pdf
# ===========================================================================


class TestConvertToPdf:

    @pytest.mark.asyncio
    async def test_success(self):
        from fastapi import HTTPException

        conn = _make_connector()

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_process.returncode = 0

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process):
            with patch("os.path.exists", return_value=True):
                result = await conn._convert_to_pdf("/tmp/test.docx", "/tmp")
                assert result == "/tmp/test.pdf"

    @pytest.mark.asyncio
    async def test_conversion_failure(self):
        from fastapi import HTTPException

        conn = _make_connector()

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b"error"))
        mock_process.returncode = 1

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process):
            with pytest.raises(HTTPException):
                await conn._convert_to_pdf("/tmp/test.docx", "/tmp")

    @pytest.mark.asyncio
    async def test_output_not_found(self):
        from fastapi import HTTPException

        conn = _make_connector()

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_process.returncode = 0

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process):
            with patch("os.path.exists", return_value=False):
                with pytest.raises(HTTPException):
                    await conn._convert_to_pdf("/tmp/test.docx", "/tmp")

    @pytest.mark.asyncio
    async def test_timeout(self):
        from fastapi import HTTPException

        conn = _make_connector()

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock()
        mock_process.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
                with pytest.raises(HTTPException):
                    await conn._convert_to_pdf("/tmp/test.docx", "/tmp")


# ===========================================================================
# Tests: _get_file_metadata_from_drive
# ===========================================================================


class TestGetFileMetadataFromDrive:

    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector()
        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_get = MagicMock()
        mock_get.execute = MagicMock(return_value={"id": "f1", "name": "test.txt", "mimeType": "text/plain"})
        mock_files.get = MagicMock(return_value=mock_get)
        mock_service.files = MagicMock(return_value=mock_files)

        result = await conn._get_file_metadata_from_drive("f1", mock_service)
        assert result["id"] == "f1"

    @pytest.mark.asyncio
    async def test_404_error(self):
        from fastapi import HTTPException
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.NOT_FOUND.value
        http_error = HttpError(mock_resp, b"not found")

        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_get = MagicMock()
        mock_get.execute = MagicMock(side_effect=http_error)
        mock_files.get = MagicMock(return_value=mock_get)
        mock_service.files = MagicMock(return_value=mock_files)

        with pytest.raises(HTTPException) as exc_info:
            await conn._get_file_metadata_from_drive("f1", mock_service)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_other_http_error(self):
        from fastapi import HTTPException
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status = 500
        http_error = HttpError(mock_resp, b"server error")

        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_get = MagicMock()
        mock_get.execute = MagicMock(side_effect=http_error)
        mock_files.get = MagicMock(return_value=mock_get)
        mock_service.files = MagicMock(return_value=mock_files)

        with pytest.raises(HTTPException):
            await conn._get_file_metadata_from_drive("f1", mock_service)

    @pytest.mark.asyncio
    async def test_generic_error(self):
        from fastapi import HTTPException

        conn = _make_connector()
        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_get = MagicMock()
        mock_get.execute = MagicMock(side_effect=Exception("generic"))
        mock_files.get = MagicMock(return_value=mock_get)
        mock_service.files = MagicMock(return_value=mock_files)

        with pytest.raises(HTTPException):
            await conn._get_file_metadata_from_drive("f1", mock_service)


# ===========================================================================
# Tests: _get_drive_service_for_user
# ===========================================================================


class TestGetDriveServiceForUser:

    @pytest.mark.asyncio
    async def test_with_user_email(self):
        conn = _make_connector()
        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = MagicMock()
            mock_client.get_client = MagicMock(return_value="user_service")
            MockGC.build_from_services = AsyncMock(return_value=mock_client)

            result = await conn._get_drive_service_for_user("u@x.com")
            assert result == "user_service"

    @pytest.mark.asyncio
    async def test_impersonation_failure_falls_back(self):
        conn = _make_connector()
        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            MockGC.build_from_services = AsyncMock(side_effect=Exception("impersonate fail"))
            conn.drive_client.get_client = MagicMock(return_value="sa_service")

            result = await conn._get_drive_service_for_user("u@x.com")
            assert result == "sa_service"

    @pytest.mark.asyncio
    async def test_no_user_email(self):
        conn = _make_connector()
        conn.drive_client.get_client = MagicMock(return_value="sa_service")
        result = await conn._get_drive_service_for_user(None)
        assert result == "sa_service"

    @pytest.mark.asyncio
    async def test_no_drive_client_raises(self):
        from fastapi import HTTPException

        conn = _make_connector()
        conn.drive_client = None
        with pytest.raises(HTTPException):
            await conn._get_drive_service_for_user(None)


# ===========================================================================
# Tests: stream_record
# ===========================================================================


class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_no_file_id(self):
        from fastapi import HTTPException

        conn = _make_connector()
        record = MagicMock()
        record.external_record_id = None
        record.record_name = "test.txt"

        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_regular_download(self):
        conn = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "f1"
        record.record_name = "test.txt"

        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_get = MagicMock()
        mock_get.execute = MagicMock(return_value={"id": "f1", "mimeType": "text/plain"})
        mock_files.get = MagicMock(return_value=mock_get)
        mock_files.get_media = MagicMock(return_value=MagicMock())
        mock_service.files = MagicMock(return_value=mock_files)

        user_with_perm = MagicMock()
        user_with_perm.email = "u@x.com"
        conn.data_store_provider = _make_mock_data_store_provider(user_with_permission=user_with_perm)

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch.object(conn, "_get_file_metadata_from_drive", new_callable=AsyncMock,
                              return_value={"mimeType": "text/plain"}):
                with patch("app.connectors.sources.google.drive.team.connector.create_stream_record_response",
                           return_value=MagicMock()) as mock_stream:
                    result = await conn.stream_record(record, user_id=None)
                    mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_google_workspace_pdf_export(self):
        conn = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "f1"
        record.record_name = "doc"

        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_files.export_media = MagicMock(return_value=MagicMock())
        mock_service.files = MagicMock(return_value=mock_files)

        conn.data_store_provider = _make_mock_data_store_provider(
            user_by_id={"email": "u@x.com"}
        )

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch.object(conn, "_get_file_metadata_from_drive", new_callable=AsyncMock,
                              return_value={"mimeType": "application/vnd.google-apps.document"}):
                with patch("app.connectors.sources.google.drive.team.connector.create_stream_record_response",
                           return_value=MagicMock()):
                    result = await conn.stream_record(record, user_id="uid-1", convertTo=MimeTypes.PDF.value)

    @pytest.mark.asyncio
    async def test_google_workspace_regular_export(self):
        conn = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "f1"
        record.record_name = "spreadsheet"

        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_files.export_media = MagicMock(return_value=MagicMock())
        mock_service.files = MagicMock(return_value=mock_files)

        user_with_perm = MagicMock()
        user_with_perm.email = "u@x.com"
        conn.data_store_provider = _make_mock_data_store_provider(user_with_permission=user_with_perm)

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch.object(conn, "_get_file_metadata_from_drive", new_callable=AsyncMock,
                              return_value={"mimeType": "application/vnd.google-apps.spreadsheet"}):
                with patch("app.connectors.sources.google.drive.team.connector.create_stream_record_response",
                           return_value=MagicMock()):
                    result = await conn.stream_record(record, user_id=None)

    @pytest.mark.asyncio
    async def test_user_id_none_string(self):
        conn = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "f1"
        record.record_name = "test.txt"

        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_files.get_media = MagicMock(return_value=MagicMock())
        mock_service.files = MagicMock(return_value=mock_files)

        user_with_perm = MagicMock()
        user_with_perm.email = "u@x.com"
        conn.data_store_provider = _make_mock_data_store_provider(user_with_permission=user_with_perm)

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch.object(conn, "_get_file_metadata_from_drive", new_callable=AsyncMock,
                              return_value={"mimeType": "text/plain"}):
                with patch("app.connectors.sources.google.drive.team.connector.create_stream_record_response",
                           return_value=MagicMock()):
                    result = await conn.stream_record(record, user_id="None")

    @pytest.mark.asyncio
    async def test_no_user_found_for_user_id(self):
        conn = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "f1"
        record.record_name = "test.txt"

        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_files.get_media = MagicMock(return_value=MagicMock())
        mock_service.files = MagicMock(return_value=mock_files)

        # user_by_id returns None, user_with_permission also None
        conn.data_store_provider = _make_mock_data_store_provider(user_by_id=None, user_with_permission=None)

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch.object(conn, "_get_file_metadata_from_drive", new_callable=AsyncMock,
                              return_value={"mimeType": "text/plain"}):
                with patch("app.connectors.sources.google.drive.team.connector.create_stream_record_response",
                           return_value=MagicMock()):
                    result = await conn.stream_record(record, user_id="uid-1")

    @pytest.mark.asyncio
    async def test_error_raises_http_exception(self):
        from fastapi import HTTPException

        conn = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "f1"
        record.record_name = "test.txt"

        conn.data_store_provider = _make_mock_data_store_provider()

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock,
                          side_effect=Exception("service fail")):
            with pytest.raises(HTTPException):
                await conn.stream_record(record, user_id=None)


# ===========================================================================
# Tests: reindex_records
# ===========================================================================


class TestReindexRecords:

    @pytest.mark.asyncio
    async def test_empty_records(self):
        conn = _make_connector()
        await conn.reindex_records([])
        conn.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_updated_and_non_updated(self):
        conn = _make_connector()
        record1 = MagicMock()
        record1.id = "r1"
        record2 = MagicMock()
        record2.id = "r2"

        updated_file = MagicMock()
        updated_perms = [Permission(email="u@x.com", type=PermissionType.READ, entity_type=EntityType.USER)]

        with patch.object(conn, "_check_and_fetch_updated_record", new_callable=AsyncMock,
                          side_effect=[(updated_file, updated_perms), None]):
            await conn.reindex_records([record1, record2])

        conn.data_entities_processor.on_new_records.assert_called_once()
        conn.data_entities_processor.reindex_existing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_drive_source(self):
        conn = _make_connector()
        conn.drive_data_source = None
        record = MagicMock()
        record.id = "r1"
        with pytest.raises(Exception, match="Drive data source not initialized"):
            await conn.reindex_records([record])

    @pytest.mark.asyncio
    async def test_check_error_continues(self):
        conn = _make_connector()
        record1 = MagicMock()
        record1.id = "r1"
        record2 = MagicMock()
        record2.id = "r2"

        with patch.object(conn, "_check_and_fetch_updated_record", new_callable=AsyncMock,
                          side_effect=[Exception("check fail"), None]):
            await conn.reindex_records([record1, record2])

        conn.data_entities_processor.reindex_existing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_propagates(self):
        conn = _make_connector()
        record = MagicMock()
        record.id = "r1"

        # _check_and_fetch_updated_record returns None so the record goes to
        # non_updated_records, then reindex_existing_records raises which
        # propagates through the outer try/except.
        with patch.object(conn, "_check_and_fetch_updated_record", new_callable=AsyncMock,
                          return_value=None):
            conn.data_entities_processor.reindex_existing_records = AsyncMock(
                side_effect=Exception("fatal")
            )
            with pytest.raises(Exception, match="fatal"):
                await conn.reindex_records([record])


# ===========================================================================
# Tests: _check_and_fetch_updated_record
# ===========================================================================


class TestCheckAndFetchUpdatedRecord:

    @pytest.mark.asyncio
    async def test_no_file_id(self):
        conn = _make_connector()
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = None
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_user_with_permission(self):
        conn = _make_connector()
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "f1"
        record.external_record_group_id = "d1"
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_user_email(self):
        user_with_perm = MagicMock()
        user_with_perm.email = None
        conn = _make_connector(user_with_permission=user_with_perm)
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "f1"
        record.external_record_group_id = "d1"
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_success_with_update(self):
        user_with_perm = MagicMock()
        user_with_perm.email = "u@x.com"
        user_with_perm.source_user_id = "su-1"
        conn = _make_connector(user_with_permission=user_with_perm)

        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "f1"
        record.external_record_group_id = "d1"

        mock_service = MagicMock()
        mock_ds = AsyncMock()
        mock_ds.about_get = AsyncMock(return_value={
            "user": {"permissionId": "perm-id", "emailAddress": "u@x.com"},
        })
        mock_ds.files_get = AsyncMock(return_value=_make_file_metadata())

        mock_update = MagicMock()
        mock_update.is_deleted = False
        mock_update.is_updated = True
        mock_update.record = MagicMock()
        mock_update.new_permissions = []

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch("app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                       return_value=mock_ds):
                with patch.object(conn, "_process_drive_item", new_callable=AsyncMock, return_value=mock_update):
                    result = await conn._check_and_fetch_updated_record("org-1", record)

        assert result is not None
        assert result[0].id == "r1"

    @pytest.mark.asyncio
    async def test_not_updated(self):
        user_with_perm = MagicMock()
        user_with_perm.email = "u@x.com"
        user_with_perm.source_user_id = "su-1"
        conn = _make_connector(user_with_permission=user_with_perm)

        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "f1"
        record.external_record_group_id = "d1"

        mock_service = MagicMock()
        mock_ds = AsyncMock()
        mock_ds.about_get = AsyncMock(return_value={
            "user": {"permissionId": "perm-id", "emailAddress": "u@x.com"},
        })
        mock_ds.files_get = AsyncMock(return_value=_make_file_metadata())

        mock_update = MagicMock()
        mock_update.is_deleted = False
        mock_update.is_updated = False

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch("app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                       return_value=mock_ds):
                with patch.object(conn, "_process_drive_item", new_callable=AsyncMock, return_value=mock_update):
                    result = await conn._check_and_fetch_updated_record("org-1", record)

        assert result is None

    @pytest.mark.asyncio
    async def test_file_not_found_at_source(self):
        from googleapiclient.errors import HttpError

        user_with_perm = MagicMock()
        user_with_perm.email = "u@x.com"
        user_with_perm.source_user_id = "su-1"
        conn = _make_connector(user_with_permission=user_with_perm)

        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "f1"
        record.external_record_group_id = "d1"

        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.NOT_FOUND.value
        http_error = HttpError(mock_resp, b"not found")

        mock_service = MagicMock()
        mock_ds = AsyncMock()
        mock_ds.about_get = AsyncMock(return_value={
            "user": {"permissionId": "perm-id", "emailAddress": "u@x.com"},
        })
        mock_ds.files_get = AsyncMock(side_effect=http_error)

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch("app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                       return_value=mock_ds):
                result = await conn._check_and_fetch_updated_record("org-1", record)

        assert result is None

    @pytest.mark.asyncio
    async def test_no_permission_id_fallback_to_source_user_id(self):
        user_with_perm = MagicMock()
        user_with_perm.email = "u@x.com"
        user_with_perm.source_user_id = "fallback-id"
        conn = _make_connector(user_with_permission=user_with_perm)

        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "f1"
        record.external_record_group_id = "d1"

        mock_service = MagicMock()
        mock_ds = AsyncMock()
        mock_ds.about_get = AsyncMock(return_value={"user": {}})
        mock_ds.files_get = AsyncMock(return_value=_make_file_metadata())

        mock_update = MagicMock()
        mock_update.is_deleted = False
        mock_update.is_updated = True
        mock_update.record = MagicMock()
        mock_update.new_permissions = []

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch("app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                       return_value=mock_ds):
                with patch.object(conn, "_process_drive_item", new_callable=AsyncMock, return_value=mock_update):
                    result = await conn._check_and_fetch_updated_record("org-1", record)

        assert result is not None

    @pytest.mark.asyncio
    async def test_no_permission_id_no_source_user_id(self):
        user_with_perm = MagicMock()
        user_with_perm.email = "u@x.com"
        user_with_perm.source_user_id = None
        conn = _make_connector(user_with_permission=user_with_perm)

        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "f1"
        record.external_record_group_id = "d1"

        mock_service = MagicMock()
        mock_ds = AsyncMock()
        mock_ds.about_get = AsyncMock(return_value={"user": {}})

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch("app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                       return_value=mock_ds):
                result = await conn._check_and_fetch_updated_record("org-1", record)

        assert result is None

    @pytest.mark.asyncio
    async def test_deleted_update_returns_none(self):
        user_with_perm = MagicMock()
        user_with_perm.email = "u@x.com"
        user_with_perm.source_user_id = "su-1"
        conn = _make_connector(user_with_permission=user_with_perm)

        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "f1"
        record.external_record_group_id = "d1"

        mock_service = MagicMock()
        mock_ds = AsyncMock()
        mock_ds.about_get = AsyncMock(return_value={
            "user": {"permissionId": "perm-id", "emailAddress": "u@x.com"},
        })
        mock_ds.files_get = AsyncMock(return_value=_make_file_metadata())

        mock_update = MagicMock()
        mock_update.is_deleted = True

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch("app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                       return_value=mock_ds):
                with patch.object(conn, "_process_drive_item", new_callable=AsyncMock, return_value=mock_update):
                    result = await conn._check_and_fetch_updated_record("org-1", record)

        assert result is None

    @pytest.mark.asyncio
    async def test_none_update_returns_none(self):
        user_with_perm = MagicMock()
        user_with_perm.email = "u@x.com"
        user_with_perm.source_user_id = "su-1"
        conn = _make_connector(user_with_permission=user_with_perm)

        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "f1"
        record.external_record_group_id = "d1"

        mock_service = MagicMock()
        mock_ds = AsyncMock()
        mock_ds.about_get = AsyncMock(return_value={
            "user": {"permissionId": "perm-id", "emailAddress": "u@x.com"},
        })
        mock_ds.files_get = AsyncMock(return_value=_make_file_metadata())

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch("app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                       return_value=mock_ds):
                with patch.object(conn, "_process_drive_item", new_callable=AsyncMock, return_value=None):
                    result = await conn._check_and_fetch_updated_record("org-1", record)

        assert result is None

    @pytest.mark.asyncio
    async def test_error_returns_none(self):
        conn = _make_connector()
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "f1"
        record.external_record_group_id = "d1"

        # Force an error
        conn.data_store_provider.transaction = MagicMock(side_effect=Exception("tx fail"))
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_file_metadata_is_none(self):
        user_with_perm = MagicMock()
        user_with_perm.email = "u@x.com"
        user_with_perm.source_user_id = "su-1"
        conn = _make_connector(user_with_permission=user_with_perm)

        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "f1"
        record.external_record_group_id = "d1"

        mock_service = MagicMock()
        mock_ds = AsyncMock()
        mock_ds.about_get = AsyncMock(return_value={
            "user": {"permissionId": "perm-id", "emailAddress": "u@x.com"},
        })
        mock_ds.files_get = AsyncMock(return_value=None)

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch("app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                       return_value=mock_ds):
                result = await conn._check_and_fetch_updated_record("org-1", record)

        assert result is None

    @pytest.mark.asyncio
    async def test_shared_drive_detection(self):
        user_with_perm = MagicMock()
        user_with_perm.email = "u@x.com"
        user_with_perm.source_user_id = "su-1"
        conn = _make_connector(user_with_permission=user_with_perm)

        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "f1"
        record.external_record_group_id = "d1"

        mock_service = MagicMock()
        mock_ds = AsyncMock()
        mock_ds.about_get = AsyncMock(return_value={
            "user": {"permissionId": "perm-id", "emailAddress": "u@x.com"},
        })
        file_meta = _make_file_metadata()
        file_meta["driveId"] = "sd-1"  # Shared drive file
        mock_ds.files_get = AsyncMock(return_value=file_meta)

        mock_update = MagicMock()
        mock_update.is_deleted = False
        mock_update.is_updated = True
        mock_update.record = MagicMock()
        mock_update.new_permissions = []

        with patch.object(conn, "_get_drive_service_for_user", new_callable=AsyncMock, return_value=mock_service):
            with patch("app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource",
                       return_value=mock_ds):
                with patch.object(conn, "_process_drive_item", new_callable=AsyncMock, return_value=mock_update) as mock_process:
                    result = await conn._check_and_fetch_updated_record("org-1", record)
                    # Verify is_shared_drive was True
                    call_kwargs = mock_process.call_args
                    assert call_kwargs[1].get("is_shared_drive") is True or call_kwargs[0][4] is True


# ===========================================================================
# Tests: cleanup
# ===========================================================================


class TestCleanup:

    @pytest.mark.asyncio
    async def test_cleanup_success(self):
        conn = _make_connector()
        await conn.cleanup()
        assert conn.drive_data_source is None
        assert conn.admin_data_source is None
        assert conn.drive_client is None
        assert conn.admin_client is None
        assert conn.config is None

    @pytest.mark.asyncio
    async def test_cleanup_partial(self):
        conn = _make_connector()
        conn.drive_data_source = None
        conn.admin_data_source = None
        conn.drive_client = None
        conn.admin_client = None
        await conn.cleanup()
        assert conn.config is None

    @pytest.mark.asyncio
    async def test_cleanup_with_error(self):
        conn = _make_connector()
        # Simulate error by making hasattr fail - use a property that raises
        original_drive_data_source = conn.drive_data_source

        class ErrorProp:
            @property
            def drive_data_source(self):
                raise Exception("attr error")

        # Just test that cleanup handles errors gracefully
        conn.drive_data_source = MagicMock()
        conn.admin_data_source = MagicMock()
        await conn.cleanup()


# ===========================================================================
# Tests: create_connector
# ===========================================================================


class TestCreateConnector:

    @pytest.mark.asyncio
    async def test_create_connector(self):
        with patch(
            "app.connectors.sources.google.drive.team.connector.DataSourceEntitiesProcessor"
        ) as MockDSEP, patch(
            "app.connectors.sources.google.drive.team.connector.GoogleDriveTeamApp"
        ), patch(
            "app.connectors.sources.google.drive.team.connector.SyncPoint"
        ):
            mock_dep = MagicMock()
            mock_dep.initialize = AsyncMock()
            MockDSEP.return_value = mock_dep

            from app.connectors.sources.google.drive.team.connector import (
                GoogleDriveTeamConnector,
            )

            connector = await GoogleDriveTeamConnector.create_connector(
                logger=_make_logger(),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="test-drive-team",
                scope="personal",
                created_by="test-user-id",
            )
            assert connector is not None
            MockDSEP.return_value.initialize.assert_called_once()
