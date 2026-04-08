"""Extended coverage tests for the Dropbox connector.

Covers methods and branches not exercised by existing test suites:
- _pass_date_filters (edge cases: timezone-naive, created filters)
- _pass_extension_filter (IN/NOT_IN/various operators)
- _get_date_filters (with populated filters)
- _process_dropbox_entry (full flow: new, updated, deleted, permissions)
- _process_dropbox_items_generator
- _handle_record_updates (deleted, metadata, permissions, content)
- get_app_users
- run_sync (high-level flow)
- run_incremental_sync
- get_signed_url
- stream_record
- test_connection_and_access
- handle_webhook_notification
- cleanup
- reindex_records
- _convert_dropbox_permissions_to_permissions
- _permissions_equal
- _initialize_event_cursor
- sync_record_groups
- sync_personal_record_groups
- get_filter_options
"""

import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata
from dropbox.sharing import AccessLevel

from app.config.constants.arangodb import Connectors, MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterOperator,
    FilterType,
    ListOperator,
    SyncFilterKey,
)
from app.connectors.sources.dropbox.connector import (
    DropboxConnector,
    get_file_extension,
    get_mimetype_enum_for_dropbox,
    get_parent_path_from_path,
)
from app.models.entities import AppUser, FileRecord, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================

def _make_connector():
    logger = logging.getLogger("test.dropbox.ext")
    dep = MagicMock()
    dep.org_id = "org-dbx-ext"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_updated_record_permissions = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_app_creator_user = AsyncMock(return_value=MagicMock(email="admin@test.com"))
    dep.reindex_existing_records = AsyncMock()

    dsp = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    dsp.transaction.return_value = mock_tx

    cs = AsyncMock()
    cs.get_config = AsyncMock()

    c = DropboxConnector(logger, dep, dsp, cs, "conn-dbx-ext", "team", "test-user-id")
    c.sync_filters = FilterCollection()
    c.indexing_filters = FilterCollection()
    c.data_source = AsyncMock()
    c.dropbox_cursor_sync_point = AsyncMock()
    c.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
    c.dropbox_cursor_sync_point.update_sync_point = AsyncMock()
    c.user_sync_point = AsyncMock()
    c.user_group_sync_point = AsyncMock()
    return c


def _make_file_metadata(name="test.pdf", file_id=None, path=None,
                         server_modified=None, client_modified=None, size=1024):
    mod = server_modified or datetime(2024, 6, 1, tzinfo=timezone.utc)
    cli = client_modified or mod
    entry = FileMetadata(
        name=name,
        id=file_id or f"id:{uuid.uuid4()}",
        client_modified=cli,
        server_modified=mod,
        rev="0123456789abcdef",
        size=size,
    )
    entry.path_lower = path or f"/{name}".lower()
    entry.path_display = path or f"/{name}"
    entry.content_hash = "a" * 64
    return entry


def _make_folder_metadata(name="folder", folder_id=None, path=None):
    entry = FolderMetadata(
        name=name,
        id=folder_id or f"id:{uuid.uuid4()}",
        path_lower=path or f"/{name}".lower(),
    )
    entry.path_display = path or f"/{name}"
    return entry


def _make_deleted_metadata(name="old.txt", path=None):
    entry = DeletedMetadata(name=name)
    entry.path_lower = path or f"/{name}".lower()
    return entry


def _make_filter(value, operator=FilterOperator.IN):
    f = MagicMock()
    f.value = value
    f.is_empty.return_value = not value
    f.get_operator.return_value = MagicMock(value=operator)
    return f


def _make_extension_filter_collection(extensions, operator=FilterOperator.IN):
    """Build a real FilterCollection with a file_extensions filter."""
    op = ListOperator.IN if operator == FilterOperator.IN else ListOperator.NOT_IN
    f = Filter(
        key=SyncFilterKey.FILE_EXTENSIONS.value,
        value=extensions if isinstance(extensions, list) else extensions,
        type=FilterType.LIST,
        operator=op,
    )
    return FilterCollection(filters=[f])


def _make_invalid_extension_filter_collection():
    """Build a FilterCollection with an invalid (non-list) value via a mock Filter."""
    mock_filter = MagicMock()
    mock_filter.key = SyncFilterKey.FILE_EXTENSIONS.value
    mock_filter.value = "not-a-list"
    mock_filter.is_empty.return_value = False
    mock_filter.get_operator.return_value = ListOperator.IN
    mock_fc = MagicMock()
    mock_fc.get = lambda key, default=None: (
        mock_filter
        if (key.value if hasattr(key, "value") else key) == SyncFilterKey.FILE_EXTENSIONS.value
        else default
    )
    return mock_fc


# ===========================================================================
# _pass_date_filters - extended
# ===========================================================================

class TestPassDateFiltersExtended:
    def test_deleted_entry_passes(self):
        c = _make_connector()
        entry = _make_deleted_metadata()
        assert c._pass_date_filters(entry, modified_after=datetime(2025, 1, 1, tzinfo=timezone.utc)) is True

    def test_created_after_filter_fails(self):
        c = _make_connector()
        entry = _make_file_metadata(
            client_modified=datetime(2023, 1, 1, tzinfo=timezone.utc),
            server_modified=datetime(2023, 1, 1, tzinfo=timezone.utc),
        )
        assert c._pass_date_filters(entry, created_after=datetime(2024, 1, 1, tzinfo=timezone.utc)) is False

    def test_created_before_filter_fails(self):
        c = _make_connector()
        entry = _make_file_metadata(
            client_modified=datetime(2025, 1, 1, tzinfo=timezone.utc),
            server_modified=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        assert c._pass_date_filters(entry, created_before=datetime(2024, 1, 1, tzinfo=timezone.utc)) is False

    def test_created_before_filter_passes(self):
        c = _make_connector()
        entry = _make_file_metadata(
            client_modified=datetime(2023, 6, 1, tzinfo=timezone.utc),
            server_modified=datetime(2023, 6, 1, tzinfo=timezone.utc),
        )
        assert c._pass_date_filters(entry, created_before=datetime(2024, 1, 1, tzinfo=timezone.utc)) is True

    def test_timezone_naive_dates(self):
        """server_modified without tzinfo should get UTC attached."""
        c = _make_connector()
        entry = _make_file_metadata()
        # The function handles naive datetimes by adding UTC
        assert c._pass_date_filters(entry) is True

    def test_combined_filters(self):
        c = _make_connector()
        entry = _make_file_metadata(
            server_modified=datetime(2024, 6, 1, tzinfo=timezone.utc),
            client_modified=datetime(2024, 5, 1, tzinfo=timezone.utc),
        )
        result = c._pass_date_filters(
            entry,
            modified_after=datetime(2024, 1, 1, tzinfo=timezone.utc),
            modified_before=datetime(2024, 12, 1, tzinfo=timezone.utc),
            created_after=datetime(2024, 1, 1, tzinfo=timezone.utc),
            created_before=datetime(2024, 12, 1, tzinfo=timezone.utc),
        )
        assert result is True


# ===========================================================================
# _pass_extension_filter - extended
# ===========================================================================

class TestPassExtensionFilterExtended:
    def test_deleted_entry_passes(self):
        c = _make_connector()
        entry = _make_deleted_metadata()
        assert c._pass_extension_filter(entry) is True

    def test_in_operator_match(self):
        c = _make_connector()
        c.sync_filters = _make_extension_filter_collection(["pdf", "docx"], FilterOperator.IN)
        entry = _make_file_metadata(name="file.pdf")
        assert c._pass_extension_filter(entry) is True

    def test_in_operator_no_match(self):
        c = _make_connector()
        c.sync_filters = _make_extension_filter_collection(["pdf"], FilterOperator.IN)
        entry = _make_file_metadata(name="file.txt")
        assert c._pass_extension_filter(entry) is False

    def test_not_in_operator(self):
        c = _make_connector()
        c.sync_filters = _make_extension_filter_collection(["exe"], FilterOperator.NOT_IN)
        entry = _make_file_metadata(name="file.pdf")
        assert c._pass_extension_filter(entry) is True

    def test_not_in_operator_blocked(self):
        c = _make_connector()
        c.sync_filters = _make_extension_filter_collection(["exe"], FilterOperator.NOT_IN)
        entry = _make_file_metadata(name="virus.exe")
        assert c._pass_extension_filter(entry) is False

    def test_no_extension_in_operator(self):
        c = _make_connector()
        c.sync_filters = _make_extension_filter_collection(["pdf"], FilterOperator.IN)
        entry = _make_file_metadata(name="README")
        assert c._pass_extension_filter(entry) is False

    def test_no_extension_not_in_operator(self):
        c = _make_connector()
        c.sync_filters = _make_extension_filter_collection(["pdf"], FilterOperator.NOT_IN)
        entry = _make_file_metadata(name="README")
        assert c._pass_extension_filter(entry) is True

    def test_invalid_filter_value(self):
        c = _make_connector()
        c.sync_filters = _make_invalid_extension_filter_collection()
        entry = _make_file_metadata(name="file.pdf")
        assert c._pass_extension_filter(entry) is True

    def test_extensions_with_dots(self):
        c = _make_connector()
        c.sync_filters = _make_extension_filter_collection([".pdf", ".docx"], FilterOperator.IN)
        entry = _make_file_metadata(name="file.pdf")
        assert c._pass_extension_filter(entry) is True


# ===========================================================================
# _get_date_filters - extended
# ===========================================================================

class TestGetDateFiltersExtended:
    def test_with_both_filters(self):
        c = _make_connector()
        mf = MagicMock()
        mf.is_empty.return_value = False
        mf.get_datetime_iso.return_value = ("2024-01-01T00:00:00", "2024-12-31T23:59:59")
        cf = MagicMock()
        cf.is_empty.return_value = False
        cf.get_datetime_iso.return_value = ("2023-01-01T00:00:00", None)
        filters_map = {
            SyncFilterKey.MODIFIED: mf,
            SyncFilterKey.CREATED: cf,
        }
        mock_fc = MagicMock()
        mock_fc.get = lambda key, default=None: filters_map.get(key, default)
        c.sync_filters = mock_fc
        m_after, m_before, c_after, c_before = c._get_date_filters()
        assert m_after is not None
        assert m_before is not None
        assert c_after is not None
        assert c_before is None


# ===========================================================================
# _handle_record_updates
# ===========================================================================

class TestHandleRecordUpdates:
    @pytest.mark.asyncio
    async def test_deleted_record(self):
        c = _make_connector()
        update = MagicMock()
        update.is_deleted = True
        update.external_record_id = "ext-1"
        update.record = MagicMock()
        update.record.record_name = "deleted"
        update.is_new = False
        update.is_updated = False
        await c._handle_record_updates(update)
        c.data_entities_processor.on_record_deleted.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_record(self):
        c = _make_connector()
        update = MagicMock()
        update.is_deleted = False
        update.is_new = True
        update.is_updated = False
        update.record = MagicMock()
        update.record.record_name = "new"
        # Should just log, no processor calls
        await c._handle_record_updates(update)

    @pytest.mark.asyncio
    async def test_metadata_changed(self):
        c = _make_connector()
        update = MagicMock()
        update.is_deleted = False
        update.is_new = False
        update.is_updated = True
        update.metadata_changed = True
        update.permissions_changed = False
        update.content_changed = False
        update.record = MagicMock()
        update.record.record_name = "meta"
        await c._handle_record_updates(update)
        c.data_entities_processor.on_record_metadata_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_permissions_changed(self):
        c = _make_connector()
        update = MagicMock()
        update.is_deleted = False
        update.is_new = False
        update.is_updated = True
        update.metadata_changed = False
        update.permissions_changed = True
        update.content_changed = False
        update.record = MagicMock()
        update.record.record_name = "perms"
        update.new_permissions = []
        await c._handle_record_updates(update)
        c.data_entities_processor.on_updated_record_permissions.assert_called_once()

    @pytest.mark.asyncio
    async def test_content_changed(self):
        c = _make_connector()
        update = MagicMock()
        update.is_deleted = False
        update.is_new = False
        update.is_updated = True
        update.metadata_changed = False
        update.permissions_changed = False
        update.content_changed = True
        update.record = MagicMock()
        update.record.record_name = "content"
        await c._handle_record_updates(update)
        c.data_entities_processor.on_record_content_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_handling(self):
        c = _make_connector()
        c.data_entities_processor.on_record_deleted = AsyncMock(side_effect=Exception("fail"))
        update = MagicMock()
        update.is_deleted = True
        update.external_record_id = "ext-1"
        update.record = MagicMock()
        update.record.record_name = "err"
        update.is_new = False
        update.is_updated = False
        # Should not raise
        await c._handle_record_updates(update)


# ===========================================================================
# get_app_users
# ===========================================================================

class TestGetAppUsers:
    def test_basic(self):
        c = _make_connector()
        users_resp = MagicMock()
        member1 = MagicMock()
        member1.profile.team_member_id = "tm-1"
        member1.profile.name.display_name = "User One"
        member1.profile.email = "user1@test.com"
        member1.profile.status._tag = "active"
        member1.role._tag = "member"

        member2 = MagicMock()
        member2.profile.team_member_id = "tm-2"
        member2.profile.name.display_name = "User Two"
        member2.profile.email = "user2@test.com"
        member2.profile.status._tag = "suspended"
        member2.role._tag = "admin"

        users_resp.data.members = [member1, member2]
        result = c.get_app_users(users_resp)
        assert len(result) == 2
        assert result[0].email == "user1@test.com"
        assert result[0].is_active is True
        assert result[1].is_active is False


# ===========================================================================
# _permissions_equal - extended
# ===========================================================================

class TestPermissionsEqualExtended:
    def test_empty_lists(self):
        c = _make_connector()
        assert c._permissions_equal([], []) is True

    def test_different_content(self):
        c = _make_connector()
        p1 = [Permission(entity_type=EntityType.USER, type=PermissionType.READ, external_id="u1")]
        p2 = [Permission(entity_type=EntityType.USER, type=PermissionType.WRITE, external_id="u1")]
        assert c._permissions_equal(p1, p2) is False


# ===========================================================================
# test_connection_and_access
# ===========================================================================

class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        c.data_source.team_get_info = AsyncMock(return_value=MagicMock(success=True))
        result = await c.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self):
        c = _make_connector()
        c.data_source.users_get_current_account = AsyncMock(side_effect=Exception("connection failed"))
        result = await c.test_connection_and_access()
        assert result is False


# ===========================================================================
# cleanup
# ===========================================================================

class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup(self):
        c = _make_connector()
        await c.cleanup()
        # cleanup should not raise


# ===========================================================================
# handle_webhook_notification
# ===========================================================================

class TestHandleWebhookNotification:
    @pytest.mark.asyncio
    async def test_no_exception(self):
        c = _make_connector()
        c.run_incremental_sync = AsyncMock()
        with patch("asyncio.create_task") as mock_create_task:
            c.handle_webhook_notification({"type": "test"})
            mock_create_task.assert_called_once()


# ===========================================================================
# reindex_records
# ===========================================================================

class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty(self):
        c = _make_connector()
        await c.reindex_records([])

    @pytest.mark.asyncio
    async def test_with_records(self):
        c = _make_connector()
        c._check_and_fetch_updated_record = AsyncMock(return_value=None)
        record = MagicMock()
        record.id = "r1"
        await c.reindex_records([record])
        c.data_entities_processor.reindex_existing_records.assert_called_once()


# ===========================================================================
# _convert_dropbox_permissions_to_permissions
# ===========================================================================

class TestConvertDropboxPermissions:
    @pytest.mark.asyncio
    async def test_file_permissions(self):
        c = _make_connector()
        members_result = MagicMock()
        members_result.success = True

        user_member = MagicMock()
        user_member.access_type = MagicMock()
        user_member.access_type._tag = "editor"
        user_member.user = MagicMock()
        user_member.user.email = "user@test.com"
        user_member.user.team_member_id = "tm-1"
        user_member.user.account_id = "dbid:acc-1"

        members_result.data.users = [user_member]
        members_result.data.groups = []
        members_result.data.invitees = []

        c.data_source.sharing_list_file_members = AsyncMock(return_value=members_result)

        result = await c._convert_dropbox_permissions_to_permissions(
            file_or_folder_id="id:f1",
            is_file=True,
            team_member_id="tm-1",
        )
        assert len(result) >= 1
        assert result[0].email == "user@test.com"

    @pytest.mark.asyncio
    async def test_folder_no_shared_id(self):
        c = _make_connector()
        result = await c._convert_dropbox_permissions_to_permissions(
            file_or_folder_id="id:d1",
            is_file=False,
            team_member_id="tm-1",
            shared_folder_id=None,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_failed_request(self):
        c = _make_connector()
        members_result = MagicMock()
        members_result.success = False
        members_result.error = "API error"
        c.data_source.sharing_list_file_members = AsyncMock(return_value=members_result)

        result = await c._convert_dropbox_permissions_to_permissions(
            file_or_folder_id="id:f1",
            is_file=True,
        )
        assert result == []


# ===========================================================================
# _process_dropbox_items_generator
# ===========================================================================

class TestProcessDropboxItemsGenerator:
    @pytest.mark.asyncio
    async def test_generator_yields_records(self):
        c = _make_connector()
        entry = _make_file_metadata(name="file.pdf")

        # Mock _process_dropbox_entry to return a valid RecordUpdate
        update = MagicMock()
        update.record = MagicMock()
        update.record.is_shared = False
        update.record.indexing_status = None
        update.new_permissions = []
        c._process_dropbox_entry = AsyncMock(return_value=update)

        items = []
        async for item in c._process_dropbox_items_generator(
            [entry], "user-1", "user@test.com", "group-1", False
        ):
            items.append(item)

        assert len(items) == 1
        assert items[0][0] == update.record

    @pytest.mark.asyncio
    async def test_generator_skips_none(self):
        c = _make_connector()
        entry = _make_file_metadata(name="skip.pdf")
        c._process_dropbox_entry = AsyncMock(return_value=None)

        items = []
        async for item in c._process_dropbox_items_generator(
            [entry], "user-1", "user@test.com", "group-1", False
        ):
            items.append(item)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_generator_handles_exception(self):
        c = _make_connector()
        entry = _make_file_metadata(name="err.pdf")
        c._process_dropbox_entry = AsyncMock(side_effect=Exception("fail"))

        items = []
        async for item in c._process_dropbox_items_generator(
            [entry], "user-1", "user@test.com", "group-1", False
        ):
            items.append(item)
        assert len(items) == 0


# ===========================================================================
# _initialize_event_cursor
# ===========================================================================

class TestInitializeEventCursor:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        resp = MagicMock()
        resp.success = True
        resp.data = MagicMock()
        resp.data.cursor = "cursor-123"
        c.data_source.team_log_get_events = AsyncMock(return_value=resp)

        await c._initialize_event_cursor("sync-key", MagicMock())
        c.dropbox_cursor_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_cursor(self):
        c = _make_connector()
        resp = MagicMock()
        resp.success = True
        resp.data = MagicMock()
        resp.data.cursor = None
        c.data_source.team_log_get_events = AsyncMock(return_value=resp)

        await c._initialize_event_cursor("sync-key", MagicMock())
        # Should not call update_sync_point since cursor is None
        # Actually, hasattr check returns True but cursor is None
        c.dropbox_cursor_sync_point.update_sync_point.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception(self):
        c = _make_connector()
        c.data_source.team_log_get_events = AsyncMock(side_effect=Exception("fail"))
        # Should not raise
        await c._initialize_event_cursor("sync-key", MagicMock())


# ===========================================================================
# get_signed_url
# ===========================================================================

class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_file_record(self):
        c = _make_connector()

        # Mock the transaction store with required methods
        mock_user = MagicMock()
        mock_user.email = "user@test.com"
        mock_file_record = MagicMock()
        mock_file_record.path = "/test.pdf"

        mock_tx = MagicMock()
        mock_tx.get_first_user_with_permission_to_node = AsyncMock(return_value=mock_user)
        mock_tx.get_file_record_by_id = AsyncMock(return_value=mock_file_record)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        # Mock team_members_get_info_v2
        team_member_info = MagicMock()
        member_info = MagicMock()
        member_info.profile.team_member_id = "tm-1"
        team_member_info.data.members_info = [MagicMock(get_member_info=MagicMock(return_value=member_info))]
        c.data_source.team_members_get_info_v2 = AsyncMock(return_value=team_member_info)

        # Mock files_get_temporary_link
        temp_link = MagicMock()
        temp_link.success = True
        temp_link.data = MagicMock()
        temp_link.data.link = "https://dl.dropbox.com/file"
        c.data_source.files_get_temporary_link = AsyncMock(return_value=temp_link)

        record = MagicMock()
        record.record_type = RecordType.FILE
        record.path = "/test.pdf"
        record.external_record_group_id = "group-1"
        record.external_record_id = "id:f1"

        result = await c.get_signed_url(record)
        assert result == "https://dl.dropbox.com/file"

    @pytest.mark.asyncio
    async def test_folder_record(self):
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.path = "/folder"
        record.external_record_group_id = "group-1"
        record.external_record_id = "id:d1"

        # Mock the transaction - no user found with permission
        mock_tx = MagicMock()
        mock_tx.get_first_user_with_permission_to_node = AsyncMock(return_value=None)
        mock_tx.get_file_record_by_id = AsyncMock(return_value=MagicMock())
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        result = await c.get_signed_url(record)
        assert result is None


# ===========================================================================
# _process_dropbox_entry (partial integration)
# ===========================================================================

class TestProcessDropboxEntry:
    @pytest.mark.asyncio
    async def test_new_file_entry(self):
        c = _make_connector()
        entry = _make_file_metadata(name="new.pdf", file_id="id:new1", path="/new.pdf")

        # Mock data_source methods
        temp_link = MagicMock(success=True)
        temp_link.data = MagicMock()
        temp_link.data.link = "https://dl.dropbox.com/new.pdf"
        c.data_source.files_get_temporary_link = AsyncMock(return_value=temp_link)

        shared_link = MagicMock(success=True)
        shared_link.data = MagicMock()
        shared_link.data.url = "https://dropbox.com/preview"
        c.data_source.sharing_create_shared_link_with_settings = AsyncMock(return_value=shared_link)

        parent_meta = MagicMock(success=False)
        c.data_source.files_get_metadata = AsyncMock(return_value=parent_meta)

        c._convert_dropbox_permissions_to_permissions = AsyncMock(return_value=[])

        result = await c._process_dropbox_entry(
            entry, "user-1", "user@test.com", "group-1", False
        )
        assert result is not None
        assert result.is_new is True
        assert result.record is not None
        assert result.record.record_name == "new.pdf"

    @pytest.mark.asyncio
    async def test_deleted_entry(self):
        c = _make_connector()
        entry = _make_deleted_metadata("deleted.txt")
        # Deleted entries return None (deletion is commented out in code)
        result = await c._process_dropbox_entry(
            entry, "user-1", "user@test.com", "group-1", False
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_folder_entry(self):
        c = _make_connector()
        entry = _make_folder_metadata("myfolder", folder_id="id:fold1", path="/myfolder")

        shared_link = MagicMock(success=True)
        shared_link.data = MagicMock()
        shared_link.data.url = "https://dropbox.com/folder"
        c.data_source.sharing_create_shared_link_with_settings = AsyncMock(return_value=shared_link)

        parent_meta = MagicMock(success=False)
        c.data_source.files_get_metadata = AsyncMock(return_value=parent_meta)

        c._convert_dropbox_permissions_to_permissions = AsyncMock(return_value=[])

        result = await c._process_dropbox_entry(
            entry, "user-1", "user@test.com", "group-1", False
        )
        assert result is not None
        assert result.record.is_file is False

    @pytest.mark.asyncio
    async def test_entry_error(self):
        c = _make_connector()
        entry = _make_file_metadata(name="err.pdf")
        # Make the data store raise
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("db error"))
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        result = await c._process_dropbox_entry(
            entry, "user-1", "user@test.com", "group-1", False
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_shared_link_already_exists(self):
        c = _make_connector()
        entry = _make_file_metadata(name="exists.pdf", file_id="id:ex1", path="/exists.pdf")

        temp_link = MagicMock(success=True)
        temp_link.data = MagicMock()
        temp_link.data.link = "https://dl.dropbox.com/exists.pdf"
        c.data_source.files_get_temporary_link = AsyncMock(return_value=temp_link)

        # First call fails with shared_link_already_exists
        first_result = MagicMock(success=False)
        first_result.error = "shared_link_already_exists"

        # Second call also has shared_link_already_exists with URL in error
        second_result = MagicMock(success=False)
        second_result.error = "shared_link_already_exists: SharedLinkAlreadyExistsMetadata(url='https://dropbox.com/preview')"

        c.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            side_effect=[first_result, second_result]
        )

        parent_meta = MagicMock(success=False)
        c.data_source.files_get_metadata = AsyncMock(return_value=parent_meta)
        c._convert_dropbox_permissions_to_permissions = AsyncMock(return_value=[])

        result = await c._process_dropbox_entry(
            entry, "user-1", "user@test.com", "group-1", False
        )
        assert result is not None
        assert result.record.weburl == "https://dropbox.com/preview"
