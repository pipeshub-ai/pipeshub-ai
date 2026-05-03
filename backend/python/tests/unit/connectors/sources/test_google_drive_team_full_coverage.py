"""Full coverage tests for GoogleDriveTeamConnector."""

import asyncio
import io
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, call, patch

import pytest
from fastapi import HTTPException
from googleapiclient.errors import HttpError

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    ExtensionTypes,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
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


def _make_logger():
    log = logging.getLogger("test_drive_team_fc")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None, user_with_perm=None, user_by_id=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
    tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user_with_perm)
    tx.get_user_by_user_id = AsyncMock(return_value=user_by_id)
    return tx


def _make_mock_data_store_provider(existing_record=None, user_with_perm=None, user_by_id=None):
    tx = _make_mock_tx_store(existing_record, user_with_perm, user_by_id)
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
    return {
        "id": user_id,
        "primaryEmail": email,
        "name": {"fullName": full_name, "givenName": given_name, "familyName": family_name},
        "suspended": suspended,
        "creationTime": creation_time,
        "organizations": organizations if organizations is not None else [{"title": "Engineer"}],
    }


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


def _make_record(record_id="rec-1", external_id="file-1", record_name="test.txt",
                 external_record_group_id="drive-1", version=0,
                 external_revision_id="rev-1", parent_external_record_id=None,
                 indexing_status=None, extraction_status=None):
    r = MagicMock(spec=Record)
    r.id = record_id
    r.external_record_id = external_id
    r.record_name = record_name
    r.external_record_group_id = external_record_group_id
    r.version = version
    r.external_revision_id = external_revision_id
    r.parent_external_record_id = parent_external_record_id
    r.indexing_status = indexing_status
    r.extraction_status = extraction_status
    r.record_type = RecordType.FILE
    r.connector_id = "drive-fc-1"
    return r


@pytest.fixture
def connector():
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
        dep.org_id = "org-1"
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_new_records = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.on_updated_record_permissions = AsyncMock()
        dep.add_permission_to_record = AsyncMock()
        dep.get_all_active_users = AsyncMock(return_value=[])
        dep.reindex_existing_records = AsyncMock()
        provider = _make_mock_data_store_provider()

        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value={
            "auth": {
                "adminEmail": "admin@example.com",
                "type": "service_account",
            }
        })

        c = GoogleDriveTeamConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=provider,
            config_service=config_svc,
            connector_id="drive-fc-1",
            scope="personal",
            created_by="test-user-id",
        )
        c.admin_data_source = AsyncMock()
        c.drive_data_source = AsyncMock()
        c.admin_client = MagicMock()
        c.drive_client = MagicMock()
        c.sync_filters = FilterCollection()
        c.indexing_filters = FilterCollection()
        yield c


class TestInit:
    @pytest.mark.asyncio
    async def test_init_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_auth(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        with pytest.raises(ValueError, match="Service account credentials not found"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_no_admin_email(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"type": "service_account"}
        })
        with pytest.raises(ValueError, match="Admin email not found"):
            await connector.init()


class TestRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_orchestrates(self, connector):
        with patch(
            "app.connectors.sources.google.drive.team.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            connector._sync_users = AsyncMock()
            connector._sync_user_groups = AsyncMock()
            connector._sync_record_groups = AsyncMock(return_value=[])
            connector._process_users_in_batches = AsyncMock()
            connector.synced_users = []
            await connector.run_sync()
            connector._sync_users.assert_awaited_once()
            connector._sync_user_groups.assert_awaited_once()
            connector._sync_record_groups.assert_awaited_once()
            connector._process_users_in_batches.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sync_error_propagates(self, connector):
        with patch(
            "app.connectors.sources.google.drive.team.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            connector._sync_users = AsyncMock(side_effect=RuntimeError("boom"))
            with pytest.raises(RuntimeError, match="boom"):
                await connector.run_sync()


class TestSyncUsers:
    @pytest.mark.asyncio
    async def test_sync_users_no_admin_source(self, connector):
        connector.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_empty(self, connector):
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": []})
        await connector._sync_users()
        assert connector.synced_users == []

    @pytest.mark.asyncio
    async def test_sync_users_pagination(self, connector):
        page1 = [_make_google_user(email="u1@t.com", user_id="u1")]
        page2 = [_make_google_user(email="u2@t.com", user_id="u2")]
        connector.admin_data_source.users_list = AsyncMock(side_effect=[
            {"users": page1, "nextPageToken": "tok2"},
            {"users": page2},
        ])
        await connector._sync_users()
        assert len(connector.synced_users) == 2
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_users_skip_no_email(self, connector):
        no_email_user = {"id": "u1", "name": {"fullName": "No Email"}}
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [no_email_user]})
        await connector._sync_users()
        assert connector.synced_users == []

    @pytest.mark.asyncio
    async def test_sync_users_suspended(self, connector):
        user = _make_google_user(suspended=True)
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        assert connector.synced_users[0].is_active is False

    @pytest.mark.asyncio
    async def test_sync_users_name_fallback_given_family(self, connector):
        user = {
            "id": "u1", "primaryEmail": "u@t.com",
            "name": {"givenName": "First", "familyName": "Last"},
            "creationTime": "2024-01-01T00:00:00.000Z",
        }
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        assert connector.synced_users[0].full_name == "First Last"

    @pytest.mark.asyncio
    async def test_sync_users_name_fallback_email(self, connector):
        user = {
            "id": "u1", "primaryEmail": "u@t.com",
            "name": {},
            "creationTime": "2024-01-01T00:00:00.000Z",
        }
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        assert connector.synced_users[0].full_name == "u@t.com"

    @pytest.mark.asyncio
    async def test_sync_users_bad_creation_time(self, connector):
        user = _make_google_user()
        user["creationTime"] = "not-a-date"
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        assert connector.synced_users[0].source_created_at is None

    @pytest.mark.asyncio
    async def test_sync_users_page_error_propagates(self, connector):
        connector.admin_data_source.users_list = AsyncMock(side_effect=RuntimeError("api err"))
        with pytest.raises(RuntimeError, match="api err"):
            await connector._sync_users()


class TestSyncUserGroups:
    @pytest.mark.asyncio
    async def test_sync_user_groups_no_admin(self, connector):
        connector.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector._sync_user_groups()

    @pytest.mark.asyncio
    async def test_sync_user_groups_empty(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(return_value={"groups": []})
        await connector._sync_user_groups()

    @pytest.mark.asyncio
    async def test_sync_user_groups_with_groups(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"email": "grp@t.com", "name": "Grp"}]
        })
        connector._process_group = AsyncMock()
        await connector._sync_user_groups()
        connector._process_group.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_user_groups_continues_on_group_error(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [
                {"email": "g1@t.com", "name": "G1"},
                {"email": "g2@t.com", "name": "G2"},
            ]
        })
        connector._process_group = AsyncMock(side_effect=[RuntimeError("fail"), None])
        await connector._sync_user_groups()
        assert connector._process_group.await_count == 2

    @pytest.mark.asyncio
    async def test_sync_user_groups_pagination(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(side_effect=[
            {"groups": [{"email": "g1@t.com", "name": "G1"}], "nextPageToken": "tok"},
            {"groups": [{"email": "g2@t.com", "name": "G2"}]},
        ])
        connector._process_group = AsyncMock()
        await connector._sync_user_groups()
        assert connector._process_group.await_count == 2


class TestProcessGroup:
    @pytest.mark.asyncio
    async def test_process_group_no_email(self, connector):
        await connector._process_group({"name": "No Email Group"})
        connector.data_entities_processor.on_new_user_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_group_with_members(self, connector):
        connector._fetch_group_members = AsyncMock(return_value=[
            {"type": "USER", "email": "m1@t.com", "id": "m1"},
        ])
        group = {"email": "grp@t.com", "name": "Grp", "creationTime": "2024-01-01T00:00:00.000Z"}
        await connector._process_group(group)
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_group_no_user_members(self, connector):
        connector._fetch_group_members = AsyncMock(return_value=[
            {"type": "GROUP", "email": "sub@t.com", "id": "g1"},
        ])
        group = {"email": "grp@t.com", "name": "Grp"}
        await connector._process_group(group)
        connector.data_entities_processor.on_new_user_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_group_member_lookup_in_synced_users(self, connector):
        connector.synced_users = [
            AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id="c1", source_user_id="m1",
                    email="m1@t.com", full_name="Member One", source_created_at=1000)
        ]
        connector._fetch_group_members = AsyncMock(return_value=[
            {"type": "USER", "email": "m1@t.com", "id": "m1"},
        ])
        group = {"email": "grp@t.com", "name": "Grp"}
        await connector._process_group(group)
        args = connector.data_entities_processor.on_new_user_groups.call_args[0][0]
        _, members = args[0]
        assert members[0].full_name == "Member One"

    @pytest.mark.asyncio
    async def test_process_group_name_fallback_email(self, connector):
        connector._fetch_group_members = AsyncMock(return_value=[
            {"type": "USER", "email": "m@t.com", "id": "m1"},
        ])
        group = {"email": "grp@t.com", "name": ""}
        await connector._process_group(group)
        args = connector.data_entities_processor.on_new_user_groups.call_args[0][0]
        user_group, _ = args[0]
        assert user_group.name == "grp@t.com"

    @pytest.mark.asyncio
    async def test_process_group_skip_member_no_email(self, connector):
        connector._fetch_group_members = AsyncMock(return_value=[
            {"type": "USER", "id": "m1"},
        ])
        group = {"email": "grp@t.com", "name": "Grp"}
        await connector._process_group(group)
        connector.data_entities_processor.on_new_user_groups.assert_not_awaited()


class TestFetchGroupMembers:
    @pytest.mark.asyncio
    async def test_fetch_group_members_pagination(self, connector):
        connector.admin_data_source.members_list = AsyncMock(side_effect=[
            {"members": [{"id": "m1"}], "nextPageToken": "tok"},
            {"members": [{"id": "m2"}]},
        ])
        result = await connector._fetch_group_members("grp@t.com")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_fetch_group_members_empty(self, connector):
        connector.admin_data_source.members_list = AsyncMock(return_value={"members": []})
        result = await connector._fetch_group_members("grp@t.com")
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_group_members_error(self, connector):
        connector.admin_data_source.members_list = AsyncMock(side_effect=RuntimeError("err"))
        with pytest.raises(RuntimeError):
            await connector._fetch_group_members("grp@t.com")


class TestMapDriveRoleToPermissionType:
    def test_owner(self, connector):
        assert connector._map_drive_role_to_permission_type("owner") == PermissionType.OWNER

    def test_organizer(self, connector):
        assert connector._map_drive_role_to_permission_type("organizer") == PermissionType.OWNER

    def test_writer(self, connector):
        assert connector._map_drive_role_to_permission_type("writer") == PermissionType.WRITE

    def test_fileorganizer(self, connector):
        assert connector._map_drive_role_to_permission_type("fileOrganizer") == PermissionType.WRITE

    def test_commenter(self, connector):
        assert connector._map_drive_role_to_permission_type("commenter") == PermissionType.COMMENT

    def test_reader(self, connector):
        assert connector._map_drive_role_to_permission_type("reader") == PermissionType.READ

    def test_unknown_defaults_read(self, connector):
        assert connector._map_drive_role_to_permission_type("bizarre") == PermissionType.READ


class TestMapDrivePermissionTypeToEntityType:
    def test_user(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("user") == EntityType.USER

    def test_group(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("group") == EntityType.GROUP

    def test_domain(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("domain") == EntityType.DOMAIN

    def test_anyone(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("anyone") == EntityType.ANYONE

    def test_anyone_with_link(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("anyoneWithLink") == EntityType.ANYONE_WITH_LINK

    def test_anyone_with_link_underscore(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("anyone_with_link") == EntityType.ANYONE_WITH_LINK

    def test_unknown_defaults_user(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("xyz") == EntityType.USER


class TestFetchPermissions:
    @pytest.mark.asyncio
    async def test_fetch_permissions_basic(self, connector):
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "user", "emailAddress": "u@t.com"},
            ]
        })
        perms, is_fallback = await connector._fetch_permissions("file-1")
        assert len(perms) == 1
        assert perms[0].type == PermissionType.READ
        assert not is_fallback

    @pytest.mark.asyncio
    async def test_fetch_permissions_skips_deleted(self, connector):
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "user", "emailAddress": "u@t.com", "deleted": True},
            ]
        })
        perms, _ = await connector._fetch_permissions("file-1")
        assert len(perms) == 0

    @pytest.mark.asyncio
    async def test_fetch_permissions_drive_http_error_raises(self, connector):
        resp = MagicMock()
        resp.status = 500
        http_err = HttpError(resp, b"Server Error")
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_err)
        with pytest.raises(HttpError):
            await connector._fetch_permissions("drive-1", is_drive=True)

    @pytest.mark.asyncio
    async def test_fetch_permissions_file_403_insufficient_with_user(self, connector):
        resp = MagicMock()
        resp.status = HttpStatusCode.FORBIDDEN.value
        http_err = HttpError(resp, b"Forbidden")
        http_err.error_details = [{"reason": "insufficientFilePermissions"}]
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_err)
        perms, is_fallback = await connector._fetch_permissions("file-1", is_drive=False, user_email="u@t.com")
        assert is_fallback is True
        assert len(perms) == 1
        assert perms[0].email == "u@t.com"

    @pytest.mark.asyncio
    async def test_fetch_permissions_file_403_no_user(self, connector):
        resp = MagicMock()
        resp.status = HttpStatusCode.FORBIDDEN.value
        http_err = HttpError(resp, b"Forbidden")
        http_err.error_details = [{"reason": "insufficientFilePermissions"}]
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_err)
        perms, is_fallback = await connector._fetch_permissions("file-1", is_drive=False)
        assert is_fallback is False
        assert perms == []

    @pytest.mark.asyncio
    async def test_fetch_permissions_file_other_http_error(self, connector):
        resp = MagicMock()
        resp.status = 500
        http_err = HttpError(resp, b"Server Error")
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_err)
        perms, is_fallback = await connector._fetch_permissions("file-1", is_drive=False)
        assert perms == []
        assert is_fallback is False

    @pytest.mark.asyncio
    async def test_fetch_permissions_file_generic_error(self, connector):
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=RuntimeError("oops"))
        perms, _ = await connector._fetch_permissions("file-1", is_drive=False)
        assert perms == []

    @pytest.mark.asyncio
    async def test_fetch_permissions_anyone_adds_fallback_for_user(self, connector):
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "anyone"},
            ]
        })
        perms, is_fallback = await connector._fetch_permissions("file-1", user_email="u@t.com")
        assert is_fallback is True
        assert any(p.email == "u@t.com" for p in perms)

    @pytest.mark.asyncio
    async def test_fetch_permissions_anyone_no_duplicate_for_existing_user(self, connector):
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [
                {"id": "p1", "role": "reader", "type": "anyone"},
                {"id": "p2", "role": "writer", "type": "user", "emailAddress": "u@t.com"},
            ]
        })
        perms, is_fallback = await connector._fetch_permissions("file-1", user_email="u@t.com")
        assert is_fallback is False

    @pytest.mark.asyncio
    async def test_fetch_permissions_pagination(self, connector):
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=[
            {"permissions": [{"id": "p1", "role": "reader", "type": "user", "emailAddress": "a@t.com"}], "nextPageToken": "tok"},
            {"permissions": [{"id": "p2", "role": "writer", "type": "user", "emailAddress": "b@t.com"}]},
        ])
        perms, _ = await connector._fetch_permissions("file-1")
        assert len(perms) == 2

    @pytest.mark.asyncio
    async def test_fetch_permissions_with_custom_data_source(self, connector):
        custom_ds = AsyncMock()
        custom_ds.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "owner", "type": "user", "emailAddress": "x@t.com"}]
        })
        perms, _ = await connector._fetch_permissions("file-1", drive_data_source=custom_ds)
        custom_ds.permissions_list.assert_awaited_once()
        assert len(perms) == 1


class TestCreateAndSyncSharedDriveRecordGroup:
    @pytest.mark.asyncio
    async def test_skip_drive_without_id(self, connector):
        await connector._create_and_sync_shared_drive_record_group({"name": "NoDrive"})
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_record_group(self, connector):
        connector._fetch_permissions = AsyncMock(return_value=([], False))
        drive = {"id": "d1", "name": "Team Drive", "createdTime": "2024-06-01T00:00:00Z"}
        await connector._create_and_sync_shared_drive_record_group(drive)
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        rg, perms = args[0]
        assert rg.name == "Team Drive"
        assert rg.external_group_id == "d1"

    @pytest.mark.asyncio
    async def test_bad_created_time(self, connector):
        connector._fetch_permissions = AsyncMock(return_value=([], False))
        drive = {"id": "d1", "name": "Drive", "createdTime": "invalid-date"}
        await connector._create_and_sync_shared_drive_record_group(drive)
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()


class TestSyncRecordGroups:
    @pytest.mark.asyncio
    async def test_no_drive_source(self, connector):
        connector.drive_data_source = None
        with pytest.raises(ValueError, match="Drive data source not initialized"):
            await connector._sync_record_groups()

    @pytest.mark.asyncio
    async def test_sync_shared_and_personal(self, connector):
        connector.drive_data_source.drives_list = AsyncMock(return_value={
            "drives": [{"id": "d1", "name": "Shared"}]
        })
        connector._create_and_sync_shared_drive_record_group = AsyncMock()
        connector._create_personal_record_group = AsyncMock()
        user = AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id="c", source_user_id="u1",
                       email="u@t.com", full_name="U")
        connector.synced_users = [user]
        result = await connector._sync_record_groups()
        assert len(result) == 1
        connector._create_and_sync_shared_drive_record_group.assert_awaited_once()
        connector._create_personal_record_group.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_continues_on_shared_drive_error(self, connector):
        connector.drive_data_source.drives_list = AsyncMock(return_value={
            "drives": [{"id": "d1", "name": "Bad"}, {"id": "d2", "name": "Good"}]
        })
        connector._create_and_sync_shared_drive_record_group = AsyncMock(
            side_effect=[RuntimeError("fail"), None]
        )
        connector._create_personal_record_group = AsyncMock()
        connector.synced_users = []
        result = await connector._sync_record_groups()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_pagination(self, connector):
        connector.drive_data_source.drives_list = AsyncMock(side_effect=[
            {"drives": [{"id": "d1", "name": "D1"}], "nextPageToken": "t"},
            {"drives": [{"id": "d2", "name": "D2"}]},
        ])
        connector._create_and_sync_shared_drive_record_group = AsyncMock()
        connector._create_personal_record_group = AsyncMock()
        connector.synced_users = []
        result = await connector._sync_record_groups()
        assert len(result) == 2


class TestCreatePersonalRecordGroup:
    @pytest.mark.asyncio
    async def test_creates_my_drive_and_shared_with_me(self, connector):
        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = AsyncMock()
            mock_client.get_client.return_value = MagicMock()
            MockGC.build_from_services = AsyncMock(return_value=mock_client)

            from app.sources.external.google.drive.drive import GoogleDriveDataSource
            with patch(
                "app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource"
            ) as MockDDS:
                mock_dds = AsyncMock()
                mock_dds.files_get = AsyncMock(return_value={"id": "root-id"})
                MockDDS.return_value = mock_dds

                user = AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id="c", source_user_id="u1",
                               email="u@t.com", full_name="User One")
                await connector._create_personal_record_group(user)
                connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
                args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
                assert len(args) == 2
                assert "My Drive" in args[0][0].description or "Drive" in args[0][0].name
                assert "Shared with Me" in args[1][0].description

    @pytest.mark.asyncio
    async def test_raises_if_no_drive_id(self, connector):
        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = AsyncMock()
            mock_client.get_client.return_value = MagicMock()
            MockGC.build_from_services = AsyncMock(return_value=mock_client)

            with patch(
                "app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource"
            ) as MockDDS:
                mock_dds = AsyncMock()
                mock_dds.files_get = AsyncMock(return_value={})
                MockDDS.return_value = mock_dds

                user = AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id="c", source_user_id="u1",
                               email="u@t.com", full_name="User One")
                with pytest.raises(Exception):
                    await connector._create_personal_record_group(user)


class TestParseDatetime:
    def test_string_date(self, connector):
        result = connector._parse_datetime("2025-01-15T00:00:00Z")
        assert result is not None
        assert isinstance(result, int)

    def test_none_returns_none(self, connector):
        assert connector._parse_datetime(None) is None

    def test_invalid_string(self, connector):
        assert connector._parse_datetime("not-a-date") is None

    def test_datetime_object(self, connector):
        dt = datetime(2025, 1, 15, tzinfo=timezone.utc)
        result = connector._parse_datetime(dt)
        assert result is not None


class TestPassDateFilters:
    def test_no_filters_passes(self, connector):
        meta = _make_file_metadata()
        assert connector._pass_date_filters(meta) is True

    def test_folder_always_passes(self, connector):
        meta = _make_file_metadata(mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value)
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2030-01-01T00:00:00Z", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        assert connector._pass_date_filters(meta) is True

    def test_created_before_start_fails(self, connector):
        meta = _make_file_metadata(created_time="2020-01-01T00:00:00Z")
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_filter if k == SyncFilterKey.CREATED else None)
        assert connector._pass_date_filters(meta) is False

    def test_created_after_end_fails(self, connector):
        meta = _make_file_metadata(created_time="2026-01-01T00:00:00Z")
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = (None, "2025-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_filter if k == SyncFilterKey.CREATED else None)
        assert connector._pass_date_filters(meta) is False

    def test_modified_before_start_fails(self, connector):
        meta = _make_file_metadata(modified_time="2020-01-01T00:00:00Z")
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_filter if k == SyncFilterKey.MODIFIED else None)
        assert connector._pass_date_filters(meta) is False


class TestPassExtensionFilter:
    def test_no_filter_passes(self, connector):
        meta = _make_file_metadata()
        assert connector._pass_extension_filter(meta) is True

    def test_folder_always_passes(self, connector):
        meta = _make_file_metadata(mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value)
        assert connector._pass_extension_filter(meta) is True

    def test_in_filter_allows_matching(self, connector):
        meta = _make_file_metadata(file_extension="pdf")
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "docx"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == SyncFilterKey.FILE_EXTENSIONS else None
        )
        assert connector._pass_extension_filter(meta) is True

    def test_in_filter_rejects_non_matching(self, connector):
        meta = _make_file_metadata(file_extension="zip")
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "docx"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == SyncFilterKey.FILE_EXTENSIONS else None
        )
        assert connector._pass_extension_filter(meta) is False

    def test_not_in_filter_allows_non_matching(self, connector):
        meta = _make_file_metadata(file_extension="zip")
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "docx"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == SyncFilterKey.FILE_EXTENSIONS else None
        )
        assert connector._pass_extension_filter(meta) is True

    def test_google_doc_mime_type_in_filter(self, connector):
        meta = _make_file_metadata(mime_type=MimeTypes.GOOGLE_DOCS.value, file_extension=None)
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = [MimeTypes.GOOGLE_DOCS.value]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == SyncFilterKey.FILE_EXTENSIONS else None
        )
        assert connector._pass_extension_filter(meta) is True

    def test_no_extension_in_filter_fails(self, connector):
        meta = {"id": "f1", "name": "noext", "mimeType": "text/plain"}
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == SyncFilterKey.FILE_EXTENSIONS else None
        )
        assert connector._pass_extension_filter(meta) is False

    def test_no_extension_not_in_filter_passes(self, connector):
        meta = {"id": "f1", "name": "noext", "mimeType": "text/plain"}
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == SyncFilterKey.FILE_EXTENSIONS else None
        )
        assert connector._pass_extension_filter(meta) is True

    def test_extension_from_name(self, connector):
        meta = {"id": "f1", "name": "file.docx", "mimeType": "application/doc"}
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["docx"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == SyncFilterKey.FILE_EXTENSIONS else None
        )
        assert connector._pass_extension_filter(meta) is True


class TestProcessDriveItem:
    @pytest.mark.asyncio
    async def test_skip_no_id(self, connector):
        result = await connector._process_drive_item({}, "uid", "u@t.com", "d1")
        assert result is None

    @pytest.mark.asyncio
    async def test_new_file(self, connector):
        meta = _make_file_metadata(parents=["parent-1"])
        connector._fetch_permissions = AsyncMock(return_value=([
            Permission(email="u@t.com", type=PermissionType.READ, entity_type=EntityType.USER)
        ], False))
        result = await connector._process_drive_item(meta, "uid", "u@t.com", "d1")
        assert result is not None
        assert result.is_new is True
        assert result.record.record_name == "test.txt"

    @pytest.mark.asyncio
    async def test_existing_file_no_change(self, connector):
        existing = _make_record(record_name="test.txt", external_revision_id="rev-1", parent_external_record_id="parent-1")
        existing.indexing_status = "indexed"
        existing.extraction_status = "done"
        provider = _make_mock_data_store_provider(existing_record=existing)
        connector.data_store_provider = provider
        meta = _make_file_metadata(parents=["parent-1"])
        connector._fetch_permissions = AsyncMock(return_value=([], False))
        result = await connector._process_drive_item(meta, "uid", "u@t.com", "d1")
        assert result is not None
        assert result.is_new is False

    @pytest.mark.asyncio
    async def test_existing_file_content_changed(self, connector):
        existing = _make_record(record_name="test.txt", external_revision_id="rev-old")
        existing.indexing_status = "indexed"
        existing.extraction_status = "done"
        existing.parent_external_record_id = "parent-1"
        provider = _make_mock_data_store_provider(existing_record=existing)
        connector.data_store_provider = provider
        meta = _make_file_metadata(parents=["parent-1"])
        connector._fetch_permissions = AsyncMock(return_value=([], False))
        result = await connector._process_drive_item(meta, "uid", "u@t.com", "d1")
        assert result.is_updated is True
        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_existing_file_name_changed(self, connector):
        existing = _make_record(record_name="old_name.txt", external_revision_id="rev-1")
        existing.indexing_status = "indexed"
        existing.extraction_status = "done"
        existing.parent_external_record_id = "parent-1"
        provider = _make_mock_data_store_provider(existing_record=existing)
        connector.data_store_provider = provider
        meta = _make_file_metadata(parents=["parent-1"])
        connector._fetch_permissions = AsyncMock(return_value=([], False))
        result = await connector._process_drive_item(meta, "uid", "u@t.com", "d1")
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_shared_with_me_detection(self, connector):
        meta = _make_file_metadata(shared=True, owners=[{"emailAddress": "other@t.com"}])
        connector._fetch_permissions = AsyncMock(return_value=([], False))
        result = await connector._process_drive_item(meta, "uid", "u@t.com", "d1")
        assert result.record.is_shared_with_me is True

    @pytest.mark.asyncio
    async def test_date_filter_skip(self, connector):
        connector._pass_date_filters = MagicMock(return_value=False)
        meta = _make_file_metadata()
        result = await connector._process_drive_item(meta, "uid", "u@t.com", "d1")
        assert result is None

    @pytest.mark.asyncio
    async def test_extension_filter_skip(self, connector):
        connector._pass_extension_filter = MagicMock(return_value=False)
        meta = _make_file_metadata()
        result = await connector._process_drive_item(meta, "uid", "u@t.com", "d1")
        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_permissions(self, connector):
        existing = _make_record()
        existing.parent_external_record_id = "parent-1"
        existing.indexing_status = "indexed"
        existing.extraction_status = "done"
        provider = _make_mock_data_store_provider(existing_record=existing)
        connector.data_store_provider = provider
        meta = _make_file_metadata(parents=["parent-1"])
        connector._fetch_permissions = AsyncMock(return_value=([
            Permission(email="u@t.com", type=PermissionType.READ, entity_type=EntityType.USER)
        ], True))
        result = await connector._process_drive_item(meta, "uid", "u@t.com", "d1")
        connector.data_entities_processor.add_permission_to_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_returns_none(self, connector):
        meta = _make_file_metadata()
        connector._pass_date_filters = MagicMock(side_effect=Exception("boom"))
        result = await connector._process_drive_item(meta, "uid", "u@t.com", "d1")
        assert result is None


class TestProcessDriveItemsGenerator:
    @pytest.mark.asyncio
    async def test_yields_records(self, connector):
        meta = _make_file_metadata()
        update = RecordUpdate(
            record=MagicMock(is_shared=False, is_shared_with_me=False),
            is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[]
        )
        connector._process_drive_item = AsyncMock(return_value=update)
        results = []
        async for item in connector._process_drive_items_generator([meta], "uid", "u@t.com", "d1"):
            results.append(item)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_skips_none_results(self, connector):
        connector._process_drive_item = AsyncMock(return_value=None)
        results = []
        async for item in connector._process_drive_items_generator([{}], "uid", "u@t.com", "d1"):
            results.append(item)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_auto_index_off_shared(self, connector):
        record = MagicMock(is_shared=True, is_shared_with_me=False)
        update = RecordUpdate(
            record=record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[]
        )
        connector._process_drive_item = AsyncMock(return_value=update)
        mock_filter = MagicMock()
        mock_filter.is_enabled = MagicMock(return_value=False)
        connector.indexing_filters = mock_filter
        results = []
        async for item in connector._process_drive_items_generator([_make_file_metadata()], "uid", "u@t.com", "d1"):
            results.append(item)
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestHandleRecordUpdates:
    @pytest.mark.asyncio
    async def test_deleted(self, connector):
        update = RecordUpdate(
            record=None, is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="f1"
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_metadata_changed(self, connector):
        record = MagicMock()
        record.record_name = "test"
        update = RecordUpdate(
            record=record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_content_changed(self, connector):
        record = MagicMock()
        record.record_name = "test"
        update = RecordUpdate(
            record=record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=False, content_changed=True, permissions_changed=False
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_content_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_permissions_changed(self, connector):
        record = MagicMock()
        record.record_name = "test"
        update = RecordUpdate(
            record=record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=True,
            new_permissions=[Permission(email="u@t.com", type=PermissionType.READ, entity_type=EntityType.USER)]
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_updated_record_permissions.assert_awaited_once()


class TestProcessDriveFilesBatch:
    @pytest.mark.asyncio
    async def test_processes_files(self, connector):
        record = MagicMock()
        perms = []
        update = RecordUpdate(
            record=record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=perms
        )

        async def gen(*a, **kw):
            yield (record, perms, update)

        connector._process_drive_items_generator = gen
        batch_records, batch_count, total = await connector._process_drive_files_batch(
            files=[{}], user_id="uid", user_email="u@t.com", drive_id="d1",
            is_shared_drive=False, context_name="test", batch_records=[], batch_count=0, total_counter=0
        )
        assert len(batch_records) == 1
        assert total == 1

    @pytest.mark.asyncio
    async def test_handles_deleted_records(self, connector):
        update = RecordUpdate(
            record=None, is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="f1"
        )
        connector._handle_record_updates = AsyncMock()

        async def gen(*a, **kw):
            yield (None, [], update)

        connector._process_drive_items_generator = gen
        batch_records, batch_count, total = await connector._process_drive_files_batch(
            files=[{}], user_id="uid", user_email="u@t.com", drive_id="d1",
            is_shared_drive=False, context_name="test", batch_records=[], batch_count=0, total_counter=0
        )
        connector._handle_record_updates.assert_awaited_once()
        assert len(batch_records) == 0


class TestProcessRemainingBatchRecords:
    @pytest.mark.asyncio
    async def test_processes_remaining(self, connector):
        batch = [("rec", "perms")]
        result, count = await connector._process_remaining_batch_records(batch, "test")
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        assert result == []
        assert count == 0

    @pytest.mark.asyncio
    async def test_empty_batch(self, connector):
        result, count = await connector._process_remaining_batch_records([], "test")
        connector.data_entities_processor.on_new_records.assert_not_awaited()
        assert result == []


class TestHandleDriveError:
    @pytest.mark.asyncio
    async def test_http_403_team_drive_membership_required(self, connector):
        resp = MagicMock()
        resp.status = HttpStatusCode.FORBIDDEN.value
        err = HttpError(resp, b"Forbidden")
        err.error_details = [{"reason": "teamDriveMembershipRequired"}]
        result = await connector._handle_drive_error(err, "Drive", "d1")
        assert result is True

    @pytest.mark.asyncio
    async def test_http_other_error(self, connector):
        resp = MagicMock()
        resp.status = 500
        err = HttpError(resp, b"Error")
        result = await connector._handle_drive_error(err, "Drive", "d1")
        assert result is True

    @pytest.mark.asyncio
    async def test_generic_error(self, connector):
        err = RuntimeError("oops")
        result = await connector._handle_drive_error(err, "Drive", "d1")
        assert result is True


class TestProcessUsersInBatches:
    @pytest.mark.asyncio
    async def test_filters_active_users(self, connector):
        active = AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id="c", source_user_id="u1",
                         email="u1@t.com", full_name="U1")
        inactive = AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id="c", source_user_id="u2",
                           email="u2@t.com", full_name="U2")
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[active])
        connector._run_sync_with_yield = AsyncMock()
        await connector._process_users_in_batches([active, inactive])
        connector._run_sync_with_yield.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_users(self, connector):
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
        connector._run_sync_with_yield = AsyncMock()
        await connector._process_users_in_batches([])
        connector._run_sync_with_yield.assert_not_awaited()


class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_returns_true_when_initialized(self, connector):
        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_no_drive_source(self, connector):
        connector.drive_data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_no_admin_source(self, connector):
        connector.admin_data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_no_clients(self, connector):
        connector.drive_client = None
        connector.admin_client = None
        result = await connector.test_connection_and_access()
        assert result is False


class TestGetSignedUrl:
    def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            connector.get_signed_url(MagicMock())


class TestRunIncrementalSync:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.run_incremental_sync()


class TestHandleWebhookNotification:
    def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("key")


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self, connector):
        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_no_drive_source(self, connector):
        connector.drive_data_source = None
        with pytest.raises(Exception, match="Drive data source not initialized"):
            await connector.reindex_records([_make_record()])

    @pytest.mark.asyncio
    async def test_reindex_with_updated_records(self, connector):
        record = _make_record()
        updated_file = MagicMock()
        updated_file.id = "rec-1"
        connector._check_and_fetch_updated_record = AsyncMock(
            return_value=(updated_file, [])
        )
        await connector.reindex_records([record])
        connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_non_updated_records(self, connector):
        record = _make_record()
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)
        await connector.reindex_records([record])
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_continues_on_check_error(self, connector):
        r1 = _make_record(record_id="r1")
        r2 = _make_record(record_id="r2")
        connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=[RuntimeError("fail"), None]
        )
        await connector.reindex_records([r1, r2])
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()


class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_missing_file_id(self, connector):
        record = _make_record(external_id=None)
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_user_with_permission(self, connector):
        record = _make_record()
        provider = _make_mock_data_store_provider(user_with_perm=None)
        connector.data_store_provider = provider
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_user_without_email(self, connector):
        user_perm = MagicMock()
        user_perm.email = None
        user_perm.source_user_id = None
        provider = _make_mock_data_store_provider(user_with_perm=user_perm)
        connector.data_store_provider = provider
        record = _make_record()
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_clears_resources(self, connector):
        await connector.cleanup()
        assert connector.drive_data_source is None
        assert connector.admin_data_source is None
        assert connector.drive_client is None
        assert connector.admin_client is None
        assert connector.config is None


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_no_file_id_raises(self, connector):
        record = MagicMock(spec=Record)
        record.external_record_id = None
        record.record_name = "test"
        record.record_type = RecordType.FILE
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_stream_regular_file(self, connector):
        record = MagicMock(spec=Record)
        record.external_record_id = "file-1"
        record.record_name = "test.pdf"
        record.id = "rec-1"

        user_mock = {"email": "u@t.com"}
        provider = _make_mock_data_store_provider(user_by_id=user_mock)
        connector.data_store_provider = provider

        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_get = MagicMock()
        mock_get.execute.return_value = {"id": "file-1", "name": "test.pdf", "mimeType": "application/pdf"}
        mock_files.get.return_value = mock_get
        mock_get_media = MagicMock()
        mock_files.get_media.return_value = mock_get_media
        mock_service.files.return_value = mock_files
        connector._get_drive_service_for_user = AsyncMock(return_value=mock_service)
        connector._get_file_metadata_from_drive = AsyncMock(return_value={
            "mimeType": "application/pdf"
        })

        with patch(
            "app.connectors.sources.google.drive.team.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector.stream_record(record, user_id="user-1")
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_google_workspace_export(self, connector):
        record = MagicMock(spec=Record)
        record.external_record_id = "file-1"
        record.record_name = "doc"
        record.id = "rec-1"

        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_export = MagicMock()
        mock_files.export_media.return_value = mock_export
        mock_service.files.return_value = mock_files

        connector._get_drive_service_for_user = AsyncMock(return_value=mock_service)
        connector._get_file_metadata_from_drive = AsyncMock(return_value={
            "mimeType": "application/vnd.google-apps.document"
        })

        user_perm = MagicMock()
        user_perm.email = "u@t.com"
        provider = _make_mock_data_store_provider(user_with_perm=user_perm)
        connector.data_store_provider = provider

        with patch(
            "app.connectors.sources.google.drive.team.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector.stream_record(record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_google_workspace_to_pdf(self, connector):
        record = MagicMock(spec=Record)
        record.external_record_id = "file-1"
        record.record_name = "doc"
        record.id = "rec-1"

        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_export = MagicMock()
        mock_files.export_media.return_value = mock_export
        mock_service.files.return_value = mock_files

        connector._get_drive_service_for_user = AsyncMock(return_value=mock_service)
        connector._get_file_metadata_from_drive = AsyncMock(return_value={
            "mimeType": "application/vnd.google-apps.spreadsheet"
        })

        user_perm = MagicMock()
        user_perm.email = "u@t.com"
        provider = _make_mock_data_store_provider(user_with_perm=user_perm)
        connector.data_store_provider = provider

        with patch(
            "app.connectors.sources.google.drive.team.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector.stream_record(record, convertTo=MimeTypes.PDF.value)
            mock_stream.assert_called_once()


class TestGetDriveServiceForUser:
    @pytest.mark.asyncio
    async def test_with_user_email(self, connector):
        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = AsyncMock()
            mock_client.get_client.return_value = MagicMock()
            MockGC.build_from_services = AsyncMock(return_value=mock_client)
            result = await connector._get_drive_service_for_user("u@t.com")
            assert result is not None

    @pytest.mark.asyncio
    async def test_without_user_email(self, connector):
        connector.drive_client = MagicMock()
        connector.drive_client.get_client.return_value = MagicMock()
        result = await connector._get_drive_service_for_user()
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_client_raises(self, connector):
        connector.drive_client = None
        with pytest.raises(HTTPException):
            await connector._get_drive_service_for_user()

    @pytest.mark.asyncio
    async def test_fallback_on_impersonation_failure(self, connector):
        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            MockGC.build_from_services = AsyncMock(side_effect=RuntimeError("fail"))
            connector.drive_client = MagicMock()
            connector.drive_client.get_client.return_value = MagicMock()
            result = await connector._get_drive_service_for_user("u@t.com")
            assert result is not None


class TestGetFileMetadataFromDrive:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_get = MagicMock()
        mock_get.execute.return_value = {"id": "f1", "name": "test", "mimeType": "text/plain"}
        mock_files.get.return_value = mock_get
        mock_service.files.return_value = mock_files
        result = await connector._get_file_metadata_from_drive("f1", mock_service)
        assert result["id"] == "f1"

    @pytest.mark.asyncio
    async def test_not_found(self, connector):
        mock_service = MagicMock()
        mock_files = MagicMock()
        resp = MagicMock()
        resp.status = HttpStatusCode.NOT_FOUND.value
        mock_files.get.return_value.execute.side_effect = HttpError(resp, b"Not Found")
        mock_service.files.return_value = mock_files
        with pytest.raises(HTTPException) as exc_info:
            await connector._get_file_metadata_from_drive("f1", mock_service)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_other_http_error(self, connector):
        mock_service = MagicMock()
        mock_files = MagicMock()
        resp = MagicMock()
        resp.status = 500
        mock_files.get.return_value.execute.side_effect = HttpError(resp, b"Error")
        mock_service.files.return_value = mock_files
        with pytest.raises(HTTPException):
            await connector._get_file_metadata_from_drive("f1", mock_service)


class TestStreamGoogleApiRequest:
    @pytest.mark.asyncio
    async def test_stream_chunks(self, connector):
        mock_request = MagicMock()
        with patch(
            "app.connectors.sources.google.drive.team.connector.MediaIoBaseDownload"
        ) as MockDownloader:
            mock_dl = MagicMock()
            mock_dl.next_chunk.side_effect = [
                (MagicMock(), False),
                (MagicMock(), True),
            ]
            MockDownloader.return_value = mock_dl
            chunks = []
            async for chunk in connector._stream_google_api_request(mock_request):
                if chunk:
                    chunks.append(chunk)


class TestSyncPersonalDrive:
    @pytest.mark.asyncio
    async def test_full_sync(self, connector):
        user = AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id="c", source_user_id="u1",
                       email="u@t.com", full_name="U")
        user_dds = AsyncMock()
        user_dds.about_get = AsyncMock(return_value={"user": {"permissionId": "p1", "emailAddress": "u@t.com"}})
        user_dds.files_get = AsyncMock(return_value={"id": "root-1"})
        user_dds.changes_get_start_page_token = AsyncMock(return_value={"startPageToken": "tok123"})
        user_dds.files_list = AsyncMock(return_value={"files": []})

        connector.drive_delta_sync_point = AsyncMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        connector._process_drive_files_batch = AsyncMock(return_value=([], 0, 0))
        connector._process_remaining_batch_records = AsyncMock(return_value=([], 0))

        await connector.sync_personal_drive(user, user_dds, "p1", "root-1")
        connector.drive_delta_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_incremental_sync(self, connector):
        user = AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id="c", source_user_id="u1",
                       email="u@t.com", full_name="U")
        user_dds = AsyncMock()
        user_dds.changes_list = AsyncMock(return_value={
            "changes": [],
            "newStartPageToken": "new-tok"
        })

        connector.drive_delta_sync_point = AsyncMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"pageToken": "old-tok-123456789012345"}
        )
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        connector._process_drive_files_batch = AsyncMock(return_value=([], 0, 0))
        connector._process_remaining_batch_records = AsyncMock(return_value=([], 0))

        await connector.sync_personal_drive(user, user_dds, "p1", "root-1")
        connector.drive_delta_sync_point.update_sync_point.assert_awaited_once()


class TestSyncSharedDrives:
    @pytest.mark.asyncio
    async def test_no_shared_drives(self, connector):
        user = AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id="c", source_user_id="u1",
                       email="u@t.com", full_name="U")
        user_dds = AsyncMock()
        user_dds.drives_list = AsyncMock(return_value={"drives": []})
        await connector.sync_shared_drives(user, user_dds, "p1")

    @pytest.mark.asyncio
    async def test_with_shared_drives_full_sync(self, connector):
        user = AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id="c", source_user_id="u1",
                       email="u@t.com", full_name="U")
        user_dds = AsyncMock()
        user_dds.drives_list = AsyncMock(return_value={
            "drives": [{"id": "sd1", "name": "Shared"}]
        })
        user_dds.changes_get_start_page_token = AsyncMock(return_value={"startPageToken": "tok"})
        user_dds.files_list = AsyncMock(return_value={"files": []})

        connector.drive_delta_sync_point = AsyncMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        connector._process_drive_files_batch = AsyncMock(return_value=([], 0, 0))
        connector._process_remaining_batch_records = AsyncMock(return_value=([], 0))

        await connector.sync_shared_drives(user, user_dds, "p1")
        connector.drive_delta_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_team_drive_membership_required_skip(self, connector):
        user = AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id="c", source_user_id="u1",
                       email="u@t.com", full_name="U")
        user_dds = AsyncMock()
        user_dds.drives_list = AsyncMock(return_value={
            "drives": [{"id": "sd1", "name": "NoAccess"}]
        })

        err = RuntimeError("nope")
        err.error_details = [{"reason": "teamDriveMembershipRequired"}]

        connector.drive_delta_sync_point = AsyncMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(side_effect=err)

        await connector.sync_shared_drives(user, user_dds, "p1")


class TestRunSyncWithYield:
    @pytest.mark.asyncio
    async def test_calls_personal_and_shared(self, connector):
        user = AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id="c", source_user_id="u1",
                       email="u@t.com", full_name="U")
        with patch(
            "app.connectors.sources.google.drive.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = AsyncMock()
            mock_client.get_client.return_value = MagicMock()
            MockGC.build_from_services = AsyncMock(return_value=mock_client)

            with patch(
                "app.connectors.sources.google.drive.team.connector.GoogleDriveDataSource"
            ) as MockDDS:
                mock_dds = AsyncMock()
                mock_dds.about_get = AsyncMock(return_value={
                    "user": {"permissionId": "p1", "emailAddress": "u@t.com"}
                })
                mock_dds.files_get = AsyncMock(return_value={"id": "root-1"})
                MockDDS.return_value = mock_dds

                connector.sync_personal_drive = AsyncMock()
                connector.sync_shared_drives = AsyncMock()
                await connector._run_sync_with_yield(user)
                connector.sync_personal_drive.assert_awaited_once()
                connector.sync_shared_drives.assert_awaited_once()
