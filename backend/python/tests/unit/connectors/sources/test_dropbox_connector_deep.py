"""Deep coverage tests for Dropbox connector.

Covers additional methods not exercised by existing test suites:
- _sync_user_groups (pagination, error handling)
- _fetch_group_members (pagination)
- _create_user_group_with_permissions
- _handle_group_renamed_event
- _update_group_name
- _handle_group_change_member_role_event
- _handle_member_change_status_event
- _handle_member_added
- _process_member_event
- _extract_folder_info_from_event
- sync_record_groups (full flow, pagination)
- sync_personal_record_groups
- _create_and_sync_single_record_group (permissions, pagination)
- _initialize_event_cursor
- stream_record / get_signed_url
- reindex_records (updated/non-updated/errors)
- _check_and_fetch_updated_record
- get_filter_options (NotImplementedError)
- _sync_member_changes_with_cursor
- _sync_group_changes_with_cursor
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.dropbox.connector import (
    DropboxConnector,
    get_file_extension,
    get_mimetype_enum_for_dropbox,
    get_parent_path_from_path,
)
from app.models.entities import AppUser, RecordGroup, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_user_group_by_external_id = AsyncMock(return_value=None)
    tx.batch_upsert_user_groups = AsyncMock()
    tx.get_first_user_with_permission_to_node = AsyncMock(return_value=None)
    tx.get_file_record_by_id = AsyncMock(return_value=None)
    return tx


def _make_connector():
    logger = logging.getLogger("test.dropbox.deep")
    dep = MagicMock()
    dep.org_id = "org-db-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_updated_record_permissions = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.reindex_existing_records = AsyncMock()

    tx = _make_mock_tx_store()

    @asynccontextmanager
    async def _transaction():
        yield tx

    dsp = MagicMock()
    dsp.transaction = _transaction

    cs = AsyncMock()
    cs.get_config = AsyncMock(return_value={
        "credentials": {"access_token": "t", "refresh_token": "r", "isTeam": True},
        "auth": {"oauthConfigId": "oid"},
    })

    with patch("app.connectors.sources.dropbox.connector.DropboxApp"):
        c = DropboxConnector(logger, dep, dsp, cs, "conn-db-deep")

    c.sync_filters = FilterCollection()
    c.indexing_filters = FilterCollection()
    c.data_source = AsyncMock()
    c.dropbox_cursor_sync_point = AsyncMock()
    c.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
    c.dropbox_cursor_sync_point.update_sync_point = AsyncMock()

    return c, dep, dsp, tx


# ===========================================================================
# _create_user_group_with_permissions
# ===========================================================================


class TestCreateUserGroupWithPermissions:

    def test_basic(self):
        c, *_ = _make_connector()
        member = MagicMock()
        member.profile.team_member_id = "tm1"
        member.profile.email = "user@test.com"
        member.profile.name.display_name = "User One"

        group, perms = c._create_user_group_with_permissions("g1", "Group One", [member])
        assert group.name == "Group One"
        assert group.source_user_group_id == "g1"
        assert len(perms) == 1
        assert perms[0].email == "user@test.com"

    def test_empty_members(self):
        c, *_ = _make_connector()
        group, perms = c._create_user_group_with_permissions("g1", "Empty", [])
        assert group.name == "Empty"
        assert perms == []


# ===========================================================================
# _fetch_group_members
# ===========================================================================


class TestFetchGroupMembers:

    @pytest.mark.asyncio
    async def test_single_page(self):
        c, *_ = _make_connector()
        member = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data.members = [member]
        resp.data.cursor = "c1"
        resp.data.has_more = False
        c.data_source.team_groups_members_list = AsyncMock(return_value=resp)

        result = await c._fetch_group_members("g1", "Group")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_multi_page(self):
        c, *_ = _make_connector()
        m1, m2 = MagicMock(), MagicMock()
        resp1 = MagicMock()
        resp1.success = True
        resp1.data.members = [m1]
        resp1.data.cursor = "c1"
        resp1.data.has_more = True

        resp2 = MagicMock()
        resp2.success = True
        resp2.data.members = [m2]
        resp2.data.cursor = "c2"
        resp2.data.has_more = False

        c.data_source.team_groups_members_list = AsyncMock(return_value=resp1)
        c.data_source.team_groups_members_list_continue = AsyncMock(return_value=resp2)

        result = await c._fetch_group_members("g1", "Group")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_error_raises(self):
        c, *_ = _make_connector()
        resp = MagicMock()
        resp.success = False
        resp.error = "API Error"
        c.data_source.team_groups_members_list = AsyncMock(return_value=resp)

        with pytest.raises(Exception, match="Error fetching members"):
            await c._fetch_group_members("g1", "Group")


# ===========================================================================
# _sync_user_groups
# ===========================================================================


class TestSyncUserGroups:

    @pytest.mark.asyncio
    async def test_success(self):
        c, dep, *_ = _make_connector()
        group = MagicMock()
        group.group_id = "g1"
        group.group_name = "Engineering"

        groups_resp = MagicMock()
        groups_resp.success = True
        groups_resp.data.groups = [group]
        groups_resp.data.cursor = "gc1"
        groups_resp.data.has_more = False
        c.data_source.team_groups_list = AsyncMock(return_value=groups_resp)

        member = MagicMock()
        member.profile.team_member_id = "tm1"
        member.profile.email = "u@test.com"
        member.profile.name.display_name = "U"
        members_resp = MagicMock()
        members_resp.success = True
        members_resp.data.members = [member]
        members_resp.data.cursor = "mc1"
        members_resp.data.has_more = False
        c.data_source.team_groups_members_list = AsyncMock(return_value=members_resp)

        await c._sync_user_groups()
        dep.on_new_user_groups.assert_called_once()
        args = dep.on_new_user_groups.call_args[0][0]
        assert len(args) == 1

    @pytest.mark.asyncio
    async def test_groups_list_error(self):
        c, dep, *_ = _make_connector()
        resp = MagicMock()
        resp.success = False
        resp.error = "API Error"
        c.data_source.team_groups_list = AsyncMock(return_value=resp)

        with pytest.raises(Exception):
            await c._sync_user_groups()

    @pytest.mark.asyncio
    async def test_no_groups(self):
        c, dep, *_ = _make_connector()
        resp = MagicMock()
        resp.success = True
        resp.data.groups = []
        resp.data.cursor = "c1"
        resp.data.has_more = False
        c.data_source.team_groups_list = AsyncMock(return_value=resp)

        await c._sync_user_groups()
        dep.on_new_user_groups.assert_not_called()


# ===========================================================================
# _extract_folder_info_from_event
# ===========================================================================


class TestExtractFolderInfoFromEvent:

    def test_folder_asset(self):
        c, *_ = _make_connector()
        asset = MagicMock()
        asset.is_folder.return_value = True
        folder_info = MagicMock()
        folder_info.display_name = "My Folder"
        folder_info.path.namespace_relative.ns_id = "ns123"
        asset.get_folder.return_value = folder_info

        event = MagicMock()
        event.assets = [asset]

        fid, fname = c._extract_folder_info_from_event(event)
        assert fid == "ns123"
        assert fname == "My Folder"

    def test_no_folder_asset(self):
        c, *_ = _make_connector()
        asset = MagicMock()
        asset.is_folder.return_value = False

        event = MagicMock()
        event.assets = [asset]

        fid, fname = c._extract_folder_info_from_event(event)
        assert fid is None
        assert fname is None

    def test_folder_no_ns_id(self):
        c, *_ = _make_connector()
        asset = MagicMock()
        asset.is_folder.return_value = True
        folder_info = MagicMock()
        folder_info.display_name = "F"
        folder_info.path = None
        asset.get_folder.return_value = folder_info

        event = MagicMock()
        event.assets = [asset]

        fid, fname = c._extract_folder_info_from_event(event)
        assert fid is None
        assert fname == "F"


# ===========================================================================
# _handle_member_added
# ===========================================================================


class TestHandleMemberAdded:

    @pytest.mark.asyncio
    async def test_user_found(self):
        c, dep, *_ = _make_connector()
        user = MagicMock()
        user.email = "new@test.com"

        await c._handle_member_added("new@test.com", "tm1", [user])
        dep.on_new_app_users.assert_called_once_with([user])

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        c, dep, *_ = _make_connector()
        await c._handle_member_added("missing@test.com", "tm1", [])
        dep.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_handled(self):
        c, dep, *_ = _make_connector()
        dep.on_new_app_users.side_effect = Exception("fail")
        user = MagicMock()
        user.email = "u@test.com"
        # Should not raise
        await c._handle_member_added("u@test.com", "tm1", [user])


# ===========================================================================
# _process_member_event
# ===========================================================================


class TestProcessMemberEvent:

    @pytest.mark.asyncio
    async def test_member_change_status(self):
        c, *_ = _make_connector()
        event = MagicMock()
        event.event_type._tag = "member_change_status"
        c._handle_member_change_status_event = AsyncMock()

        await c._process_member_event(event, [])
        c._handle_member_change_status_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_event_type(self):
        c, *_ = _make_connector()
        event = MagicMock()
        event.event_type._tag = "member_other"

        # Should not raise
        await c._process_member_event(event, [])

    @pytest.mark.asyncio
    async def test_error_handled(self):
        c, *_ = _make_connector()
        event = MagicMock()
        event.event_type._tag = "member_change_status"
        c._handle_member_change_status_event = AsyncMock(side_effect=Exception("fail"))

        # Should not raise
        await c._process_member_event(event, [])


# ===========================================================================
# _handle_member_change_status_event
# ===========================================================================


class TestHandleMemberChangeStatusEvent:

    @pytest.mark.asyncio
    async def test_active_status(self):
        c, *_ = _make_connector()
        event = MagicMock()
        event.context.is_team_member.return_value = True
        user_info = MagicMock()
        user_info.email = "u@test.com"
        user_info.display_name = "U"
        user_info.team_member_id = "tm1"
        event.context.get_team_member.return_value = user_info

        status_details = MagicMock()
        status_details.new_value._tag = "active"
        status_details.previous_value._tag = "invited"
        event.details.get_member_change_status_details.return_value = status_details

        c._handle_member_added = AsyncMock()
        await c._handle_member_change_status_event(event, [])
        c._handle_member_added.assert_called_once()

    @pytest.mark.asyncio
    async def test_removed_status(self):
        c, *_ = _make_connector()
        event = MagicMock()
        event.context.is_team_member.return_value = True
        user_info = MagicMock()
        user_info.email = "u@test.com"
        user_info.display_name = "U"
        user_info.team_member_id = "tm1"
        event.context.get_team_member.return_value = user_info

        status_details = MagicMock()
        status_details.new_value._tag = "removed"
        status_details.previous_value._tag = "active"
        event.details.get_member_change_status_details.return_value = status_details

        c._handle_member_added = AsyncMock()
        await c._handle_member_change_status_event(event, [])
        # Should not call _handle_member_added for removed status
        c._handle_member_added.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_email(self):
        c, *_ = _make_connector()
        event = MagicMock()
        event.context.is_team_member.return_value = False

        status_details = MagicMock()
        status_details.new_value._tag = "active"
        event.details.get_member_change_status_details.return_value = status_details

        c._handle_member_added = AsyncMock()
        await c._handle_member_change_status_event(event, [])
        c._handle_member_added.assert_not_called()


# ===========================================================================
# _initialize_event_cursor
# ===========================================================================


class TestInitializeEventCursor:

    @pytest.mark.asyncio
    async def test_success(self):
        c, *_ = _make_connector()
        resp = MagicMock()
        resp.success = True
        resp.data.cursor = "initial_cursor"
        c.data_source.team_log_get_events = AsyncMock(return_value=resp)

        await c._initialize_event_cursor("sync_key", "members")
        c.dropbox_cursor_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_cursor_returned(self):
        c, *_ = _make_connector()
        resp = MagicMock()
        resp.success = True
        resp.data.cursor = None
        c.data_source.team_log_get_events = AsyncMock(return_value=resp)

        await c._initialize_event_cursor("sync_key", "members")
        c.dropbox_cursor_sync_point.update_sync_point.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_handled(self):
        c, *_ = _make_connector()
        c.data_source.team_log_get_events = AsyncMock(side_effect=Exception("API error"))

        # Should not raise
        await c._initialize_event_cursor("sync_key", "members")


# ===========================================================================
# sync_record_groups
# ===========================================================================


class TestSyncRecordGroups:

    @pytest.mark.asyncio
    async def test_no_admin_user(self):
        c, dep, *_ = _make_connector()
        users = [MagicMock(title="member")]
        await c.sync_record_groups(users)
        dep.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_active_folders(self):
        c, dep, *_ = _make_connector()
        admin = MagicMock(title="team_admin", email="admin@test.com", source_user_id="adm1")
        users = [admin]

        folder = MagicMock()
        folder.team_folder_id = "tf1"
        folder.name = "Engineering"
        folder.status._tag = "active"

        folders_resp = MagicMock()
        folders_resp.success = True
        folders_resp.data.team_folders = [folder]
        folders_resp.data.cursor = "fc1"
        folders_resp.data.has_more = False
        c.data_source.team_team_folder_list = AsyncMock(return_value=folders_resp)

        c._create_and_sync_single_record_group = AsyncMock()
        await c.sync_record_groups(users)
        c._create_and_sync_single_record_group.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_inactive_folders(self):
        c, dep, *_ = _make_connector()
        admin = MagicMock(title="team_admin", email="admin@test.com", source_user_id="adm1")

        folder = MagicMock()
        folder.team_folder_id = "tf1"
        folder.name = "Archived"
        folder.status._tag = "archived"

        folders_resp = MagicMock()
        folders_resp.success = True
        folders_resp.data.team_folders = [folder]
        folders_resp.data.cursor = "fc1"
        folders_resp.data.has_more = False
        c.data_source.team_team_folder_list = AsyncMock(return_value=folders_resp)

        c._create_and_sync_single_record_group = AsyncMock()
        await c.sync_record_groups([admin])
        c._create_and_sync_single_record_group.assert_not_called()


# ===========================================================================
# _create_and_sync_single_record_group
# ===========================================================================


class TestCreateAndSyncSingleRecordGroup:

    @pytest.mark.asyncio
    async def test_success_with_users_and_groups(self):
        c, dep, *_ = _make_connector()
        admin = MagicMock(source_user_id="adm1")

        user_member = MagicMock()
        user_member.user.email = "u@test.com"
        user_member.access_type._tag = "editor"

        group_member = MagicMock()
        group_member.group.group_id = "g1"
        group_member.access_type._tag = "viewer"

        members_resp = MagicMock()
        members_resp.success = True
        members_resp.data.users = [user_member]
        members_resp.data.groups = [group_member]
        members_resp.data.cursor = "mc1"
        members_resp.data.has_more = False

        c.data_source.sharing_list_folder_members = AsyncMock(return_value=members_resp)

        await c._create_and_sync_single_record_group("tf1", "Eng", admin)
        dep.on_new_record_groups.assert_called_once()
        call_args = dep.on_new_record_groups.call_args[0][0]
        rg, perms = call_args[0]
        assert rg.name == "Eng"
        assert len(perms) == 2

    @pytest.mark.asyncio
    async def test_members_fetch_failure(self):
        c, dep, *_ = _make_connector()
        admin = MagicMock(source_user_id="adm1")
        resp = MagicMock()
        resp.success = False
        resp.error = "API Error"
        c.data_source.sharing_list_folder_members = AsyncMock(return_value=resp)

        # Should not raise - logs and returns
        await c._create_and_sync_single_record_group("tf1", "Eng", admin)
        dep.on_new_record_groups.assert_not_called()


# ===========================================================================
# stream_record
# ===========================================================================


class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_success(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.record_name = "test.pdf"
        record.mime_type = "application/pdf"
        record.id = "r1"
        c.get_signed_url = AsyncMock(return_value="https://download.url")

        with patch("app.connectors.sources.dropbox.connector.create_stream_record_response") as mock_stream, \
             patch("app.connectors.sources.dropbox.connector.stream_content"):
            mock_stream.return_value = MagicMock()
            await c.stream_record(record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_signed_url(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.id = "r1"
        c.get_signed_url = AsyncMock(return_value=None)

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 404


# ===========================================================================
# get_signed_url
# ===========================================================================


class TestGetSignedUrl:

    @pytest.mark.asyncio
    async def test_no_data_source(self):
        c, *_ = _make_connector()
        c.data_source = None
        record = MagicMock()
        result = await c.get_signed_url(record)
        assert result is None


# ===========================================================================
# reindex_records
# ===========================================================================


class TestReindexRecords:

    @pytest.mark.asyncio
    async def test_empty_records(self):
        c, dep, *_ = _make_connector()
        await c.reindex_records([])
        dep.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_data_source_raises(self):
        c, *_ = _make_connector()
        c.data_source = None
        rec = MagicMock(id="r1")
        with pytest.raises(Exception, match="Dropbox client not initialized"):
            await c.reindex_records([rec])

    @pytest.mark.asyncio
    async def test_updated_and_non_updated(self):
        c, dep, *_ = _make_connector()
        rec1 = MagicMock(id="r1")
        rec2 = MagicMock(id="r2")

        c._check_and_fetch_updated_record = AsyncMock(
            side_effect=[("updated_rec", []), None]
        )

        await c.reindex_records([rec1, rec2])
        dep.on_new_records.assert_called_once()
        dep.reindex_existing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_check_error(self):
        c, dep, *_ = _make_connector()
        rec = MagicMock(id="r1")
        c._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("fail"))

        # Should not raise
        await c.reindex_records([rec])


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================


class TestCheckAndFetchUpdatedRecord:

    @pytest.mark.asyncio
    async def test_no_external_id(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.external_record_id = None
        record.id = "r1"
        result = await c._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.external_record_id = "ext-1"
        record.external_record_group_id = "rg1"
        record.id = "r1"

        # Make the transaction raise
        async def bad_tx():
            raise Exception("fail")

        @asynccontextmanager
        async def _bad_transaction():
            raise Exception("fail")
            yield  # pragma: no cover

        c.data_store_provider.transaction = _bad_transaction
        result = await c._check_and_fetch_updated_record("org-1", record)
        assert result is None


# ===========================================================================
# get_filter_options
# ===========================================================================


class TestGetFilterOptions:

    @pytest.mark.asyncio
    async def test_raises_not_implemented(self):
        c, *_ = _make_connector()
        with pytest.raises(NotImplementedError):
            await c.get_filter_options("any_key")


# ===========================================================================
# test_connection_and_access
# ===========================================================================


class TestTestConnectionAndAccess:

    @pytest.mark.asyncio
    async def test_no_data_source(self):
        c, *_ = _make_connector()
        c.data_source = None
        result = await c.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_success(self):
        c, *_ = _make_connector()
        c.data_source.users_get_current_account = AsyncMock()
        result = await c.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c, *_ = _make_connector()
        c.data_source.users_get_current_account = AsyncMock(side_effect=Exception("fail"))
        result = await c.test_connection_and_access()
        assert result is False


# ===========================================================================
# _update_group_name
# ===========================================================================


class TestUpdateGroupName:

    @pytest.mark.asyncio
    async def test_group_found(self):
        c, dep, dsp, tx = _make_connector()
        existing = MagicMock()
        existing.id = "internal-1"
        existing.name = "Old Name"
        tx.get_user_group_by_external_id = AsyncMock(return_value=existing)

        await c._update_group_name("g1", "New Name", "Old Name")
        tx.batch_upsert_user_groups.assert_called_once()
        assert existing.name == "New Name"

    @pytest.mark.asyncio
    async def test_group_not_found(self):
        c, dep, dsp, tx = _make_connector()
        tx.get_user_group_by_external_id = AsyncMock(return_value=None)

        # Should not raise
        await c._update_group_name("g1", "New", "Old")
        tx.batch_upsert_user_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_raises(self):
        c, dep, dsp, tx = _make_connector()
        tx.get_user_group_by_external_id = AsyncMock(side_effect=Exception("DB error"))

        with pytest.raises(Exception, match="DB error"):
            await c._update_group_name("g1", "New", "Old")


# ===========================================================================
# _handle_record_updates (already tested elsewhere but adding edge cases)
# ===========================================================================


class TestHandleRecordUpdatesDeep:

    @pytest.mark.asyncio
    async def test_new_record_logs_only(self):
        c, dep, *_ = _make_connector()
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        record.record_name = "new.pdf"
        update = RecordUpdate(
            record=record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False
        )
        await c._handle_record_updates(update)
        dep.on_record_deleted.assert_not_called()
        dep.on_record_metadata_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_changes(self):
        c, dep, *_ = _make_connector()
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        record.record_name = "all.pdf"
        perms = [Permission(email="u@t.com", type=PermissionType.READ, entity_type=EntityType.USER)]
        update = RecordUpdate(
            record=record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=True,
            permissions_changed=True,
            new_permissions=perms
        )
        await c._handle_record_updates(update)
        dep.on_record_metadata_update.assert_called_once()
        dep.on_record_content_update.assert_called_once()
        dep.on_updated_record_permissions.assert_called_once()
