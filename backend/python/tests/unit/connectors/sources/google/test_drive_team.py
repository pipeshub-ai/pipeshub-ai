"""Tests for GoogleDriveTeamConnector (app/connectors/sources/google/drive/team/connector.py)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
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
        dep.get_record_by_external_id = AsyncMock(return_value=None)

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
        conn._shared_drives_listed = True  # as after a run's listing; tests set the drives listed
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
        # Stored all the same, with no direct user members.
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()
        [(_, members)] = connector.data_entities_processor.on_new_user_groups.call_args[0][0]
        assert members == []


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
        assert connector._map_drive_role_to_permission_type("commenter") == PermissionType.READ

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
        assert connector._map_drive_permission_type_to_entity_type("domain") is None

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

        perms, is_fallback, _ = await connector._fetch_permissions("file-1", is_drive=False)
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

        perms, _, _ = await connector._fetch_permissions("file-1", is_drive=False)
        assert len(perms) == 0

    async def test_403_creates_fallback_permission_for_file(self, connector):
        from googleapiclient.errors import HttpError

        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "insufficientFilePermissions"}]

        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        perms, is_fallback, _ = await connector._fetch_permissions(
            "file-1", is_drive=False, user_email="user@example.com"
        )
        assert is_fallback is True
        assert len(perms) == 1
        assert perms[0].email == "user@example.com"
        assert perms[0].type == PermissionType.READ

    async def test_empty_list_on_shared_drive_item_still_grants_the_viewer(self, connector) -> None:
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})

        perms, is_fallback, _ = await connector._fetch_permissions(
            "file-1", is_drive=False, user_email="viewer@example.com"
        )
        assert is_fallback is True
        assert [p.email for p in perms] == ["viewer@example.com"]

    async def test_empty_list_on_limited_folder_adds_no_grant_and_does_not_fail(self, connector) -> None:
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})

        perms, is_fallback, shared = await connector._fetch_permissions(
            "folder-1", is_drive=False, user_email="viewer@example.com",
            inherited_permissions_disabled=True,
        )
        assert (perms, is_fallback, shared) == ([], True, [])

    async def test_403_on_limited_folder_adds_no_grant_and_does_not_fail(self, connector) -> None:
        from googleapiclient.errors import HttpError

        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "insufficientFilePermissions"}]
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        perms, is_fallback, _ = await connector._fetch_permissions(
            "folder-1", is_drive=False, user_email="viewer@example.com",
            inherited_permissions_disabled=True,
        )
        assert (perms, is_fallback) == ([], True)

    async def test_quota_403_on_limited_folder_still_raises(self, connector) -> None:
        from googleapiclient.errors import HttpError

        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"rate")
        http_error.error_details = [{"reason": "userRateLimitExceeded"}]
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        with pytest.raises(HttpError):
            await connector._fetch_permissions(
                "folder-1", is_drive=False, user_email="viewer@example.com",
                inherited_permissions_disabled=True,
            )

    async def test_empty_list_for_a_user_who_can_share_the_item_is_an_unreadable_answer(self, connector) -> None:
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})

        with pytest.raises(RuntimeError, match="can share"):
            await connector._fetch_permissions(
                "file-1", is_drive=False, user_email="editor@example.com", can_share=True,
            )

    async def test_list_whose_entries_are_all_left_out_by_the_connector_still_gives_the_sharer_a_fallback(self, connector) -> None:
        # a suspended owner is dropped by the connector: the source did answer with an entry
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": [
            {"id": "p1", "role": "owner", "type": "user", "emailAddress": "gone@example.com", "deleted": True},
        ]})

        perms, is_fallback, _ = await connector._fetch_permissions(
            "file-1", is_drive=False, user_email="editor@example.com", can_share=True,
        )
        assert is_fallback is True
        assert [p.email for p in perms] == ["editor@example.com"]

    async def test_reindex_of_an_item_whose_permissions_could_not_be_read_is_not_queued_as_unchanged(self, connector) -> None:
        from app.connectors.sources.google.drive.team.connector import FullSyncLedger

        record = MagicMock(external_record_id="file-1", external_record_group_id="sd-1", id="rec-1")
        service = connector.drive_data_source.client
        metadata = _make_file_metadata(file_id="file-1", name="a.txt", parents=["sd-1"])
        metadata["capabilities"] = {"canShare": True}
        metadata["driveId"] = "sd-1"
        connector._get_drive_service_with_fallback = AsyncMock(
            return_value=(service, "editor@example.com", ("pid", "editor@example.com", metadata))
        )
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        real_process = connector._process_drive_item

        async def process_with_a_ledger_swapped_meanwhile(*args: object, **kwargs: object) -> object:
            # a sync starting meanwhile replaces the ledger: no count taken before the call can be compared after it
            connector._sync_ledger = FullSyncLedger()
            return await real_process(*args, **kwargs)

        connector._process_drive_item = process_with_a_ledger_swapped_meanwhile
        with patch("app.connectors.sources.google.drive.team.connector.get_impersonation_candidates",
                   AsyncMock(return_value=["editor@example.com"])):
            with pytest.raises(Exception, match="file-1"):
                await connector._check_and_fetch_updated_record("org-1", record)

            connector.data_entities_processor.reindex_existing_records = AsyncMock()
            connector.data_entities_processor.on_new_records = AsyncMock()
            connector.data_store_provider.compare_and_set_indexing_status = AsyncMock(return_value=["rec-1"])
            await connector.reindex_records([record])
        connector.data_entities_processor.reindex_existing_records.assert_not_awaited()
        # the record the caller marked NOT_STARTED is left FAILED, which a retry of failed records selects
        connector.data_store_provider.compare_and_set_indexing_status.assert_awaited_once_with(
            ["rec-1"], "NOT_STARTED", "FAILED"
        )

    async def test_item_read_failure_is_raised_only_when_asked(self, connector) -> None:
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        metadata = _make_file_metadata(file_id="file-1", name="a.txt", parents=["sd-1"])
        metadata["capabilities"] = {"canShare": True}
        kwargs = {"user_id": "u1", "user_email": "editor@example.com", "drive_id": "sd-1", "is_shared_drive": True}

        assert await connector._process_drive_item(metadata=metadata, **kwargs) is None
        with pytest.raises(Exception, match="file-1"):
            await connector._process_drive_item(metadata=metadata, raise_on_failure=True, **kwargs)

    async def test_item_whose_list_is_empty_for_a_user_who_can_share_it_is_failed_not_written(self, connector) -> None:
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        metadata = _make_file_metadata(file_id="file-1", name="a.txt", parents=["sd-1"])
        metadata["capabilities"] = {"canShare": True}

        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="editor@example.com",
            drive_id="sd-1", is_shared_drive=True,
        )

        assert result is None
        assert connector._sync_ledger.item_failures == 1

    async def test_item_whose_list_is_empty_for_a_user_who_cannot_share_it_still_gives_them_read(self, connector) -> None:
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        metadata = _make_file_metadata(file_id="file-1", name="a.txt", parents=["sd-1"])
        metadata["capabilities"] = {"canShare": False}

        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="viewer@example.com",
            drive_id="sd-1", is_shared_drive=True,
        )

        assert result is not None
        assert [p.email for p in result.new_permissions] == ["viewer@example.com"]
        assert connector._sync_ledger.item_failures == 0

    @pytest.mark.parametrize("answer", [{"id": "sd-1"}, {"id": "sd-1", "restrictions": "x"}])
    @pytest.mark.parametrize("stored", [
        {},
        {"hidden_groups": [], "group_hashes": []},
        {"driveMembersOnly": False, "domainUsersOnly": False},  # looser flags than a drive tightened since
        {"driveMembersOnly": False, "domainUsersOnly": True},
    ])
    async def test_a_drive_answer_without_a_restrictions_object_is_read_as_restricted_whatever_is_stored(
        self, connector, answer, stored
    ) -> None:
        # Unknown is not unrestricted, and stored flags may be stale: both flags only narrow access, and this run
        # reads the drive again under them so they reach every item. The stored flags stay; the mark is saved last.
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=stored)
        connector.drive_delta_sync_point.delete_sync_point = AsyncMock()
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()
        connector.drive_data_source.drives_get = AsyncMock(return_value=answer)
        connector._drive_restrictions = {}
        connector.synced_users = [MagicMock(source_user_id="u1")]

        await connector._reconcile_drive_restrictions({"id": "sd-1"})

        assert await connector._drive_restrictions_of("sd-1") == {"driveMembersOnly": True, "domainUsersOnly": True}
        assert connector._sync_ledger.has_gaps
        deleted = [c.args[0].split("/", 1)[1] for c in connector.drive_delta_sync_point.delete_sync_point.await_args_list]
        assert deleted == ["drives/u1_sd-1"]
        writes = [(c.args[0].split("/", 1)[-1], c.args[1]) for c in connector.drive_delta_sync_point.update_sync_point.await_args_list]
        assert writes == [("users/u1", {"reseed_shared_with_me": True, "failed_items": []}), ("shared_drive_restrictions_sd-1", {"assumed": True})]

    async def test_a_drive_still_answering_without_restrictions_is_not_read_again(self, connector) -> None:
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"driveMembersOnly": False, "domainUsersOnly": False, "assumed": True}
        )
        connector.drive_delta_sync_point.delete_sync_point = AsyncMock()
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()
        connector.drive_data_source.drives_get = AsyncMock(return_value={"id": "sd-1"})
        connector._drive_restrictions = {}
        connector.synced_users = [MagicMock(source_user_id="u1")]

        await connector._reconcile_drive_restrictions({"id": "sd-1"})

        assert await connector._drive_restrictions_of("sd-1") == {"driveMembersOnly": True, "domainUsersOnly": True}
        connector.drive_delta_sync_point.delete_sync_point.assert_not_awaited()
        connector.drive_delta_sync_point.update_sync_point.assert_not_awaited()

    @pytest.mark.parametrize("stored", [{"assumed": True}, {"driveMembersOnly": True, "domainUsersOnly": False, "assumed": True}])
    async def test_the_first_answer_with_restrictions_after_an_assumed_run_lists_the_drive_in_full(self, connector, stored) -> None:
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=stored)
        connector.drive_delta_sync_point.delete_sync_point = AsyncMock()
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()
        connector._drive_restrictions = {}
        connector.synced_users = [MagicMock(source_user_id="u1")]

        await connector._reconcile_drive_restrictions({"id": "sd-1", "restrictions": {}})

        # The drive is read from the start; the user's own files shared with them are read again, token kept.
        deleted = [c.args[0] for c in connector.drive_delta_sync_point.delete_sync_point.await_args_list]
        assert [k.split("/", 1)[1] for k in deleted] == ["drives/u1_sd-1"]
        writes = {c.args[0].split("/", 1)[-1]: c.args[1] for c in connector.drive_delta_sync_point.update_sync_point.await_args_list}
        assert writes == {
            "users/u1": {"reseed_shared_with_me": True, "failed_items": []},
            "shared_drive_restrictions_sd-1": {
                "driveMembersOnly": False, "domainUsersOnly": False, "hidden_groups": [], "group_hashes": [], "assumed": False,
            },
        }

    def test_a_shared_read_keeps_its_mark_and_the_failed_ids_until_none_remain(self, connector, caplog) -> None:
        assert connector._reseed_fields(set(), asked=False) == {}
        assert connector._reseed_fields(set(), asked=True) == {"reseed_shared_with_me": False, "failed_items": []}
        assert connector._reseed_fields({"f2", "f1"}, asked=False) == {"reseed_shared_with_me": True, "failed_items": ["f1", "f2"]}
        # Past the cap the ids are not kept: the whole listing is read again. The log names a count only.
        many = {f"secret-{i}" for i in range(501)}
        assert connector._reseed_fields(many, asked=True) == {"reseed_shared_with_me": True, "failed_items": []}
        assert "secret-" not in caplog.text and "501" in caplog.text

    async def test_too_many_failed_items_drop_the_checkpoint_so_the_listing_is_read_again(self, connector) -> None:
        connector.drive_delta_sync_point.delete_sync_point = AsyncMock()

        assert await connector._keep_failed_items("k", {"f1"}) is True
        connector.drive_delta_sync_point.delete_sync_point.assert_not_awaited()
        assert await connector._keep_failed_items("k", {f"f{i}" for i in range(501)}) is False
        connector.drive_delta_sync_point.delete_sync_point.assert_awaited_once_with("k")

    async def test_empty_list_for_a_member_of_a_limited_folder_gives_their_own_fallback(self, connector) -> None:
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})

        perms, is_fallback, _ = await connector._fetch_permissions(
            "folder-1", is_drive=False, user_email="member@example.com",
            inherited_permissions_disabled=True, can_list_children=True,
        )
        assert is_fallback is True
        assert [(p.email, p.type) for p in perms] == [("member@example.com", PermissionType.READ)]

    async def test_403_for_a_member_of_a_limited_folder_gives_their_own_fallback(self, connector) -> None:
        from googleapiclient.errors import HttpError

        mock_resp = MagicMock()
        mock_resp.status = HttpStatusCode.FORBIDDEN.value
        http_error = HttpError(mock_resp, b"forbidden")
        http_error.error_details = [{"reason": "insufficientFilePermissions"}]
        connector.drive_data_source.permissions_list = AsyncMock(side_effect=http_error)

        perms, is_fallback, _ = await connector._fetch_permissions(
            "folder-1", is_drive=False, user_email="member@example.com",
            inherited_permissions_disabled=True, can_list_children=True,
        )
        assert is_fallback is True
        assert [(p.email, p.type) for p in perms] == [("member@example.com", PermissionType.READ)]

    async def test_item_for_a_member_of_a_limited_folder_is_not_failed_and_is_a_fallback(self, connector) -> None:
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        metadata = _make_file_metadata(
            file_id="folder-1", name="Folder", mime_type="application/vnd.google-apps.folder",
            parents=["sd-1"], file_extension=None,
        )
        metadata["inheritedPermissionsDisabled"] = True
        metadata["capabilities"] = {"canListChildren": True}

        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="member@example.com",
            drive_id="sd-1", is_shared_drive=True,
        )

        assert result is not None
        assert result.permissions_changed is False  # a fallback is never written as the item's list
        assert [p.email for p in result.new_permissions] == ["member@example.com"]

    async def test_user_on_limited_folder_writes_the_real_grants(self, connector) -> None:
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "p1", "role": "reader", "type": "user", "emailAddress": "member@example.com"}],
        })

        perms, is_fallback, _ = await connector._fetch_permissions(
            "folder-1", is_drive=False, user_email="member@example.com",
            inherited_permissions_disabled=True,
        )
        assert is_fallback is False
        assert [p.email for p in perms] == ["member@example.com"]

    async def test_item_on_limited_folder_for_a_user_not_on_it_keeps_stored_grants(self, connector) -> None:
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        metadata = _make_file_metadata(
            file_id="folder-1", name="Folder", mime_type="application/vnd.google-apps.folder",
            parents=["sd-1"], file_extension=None,
        )
        metadata["inheritedPermissionsDisabled"] = True

        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="viewer@example.com",
            drive_id="sd-1", is_shared_drive=True,
        )

        assert result is not None
        assert result.new_permissions == []
        assert result.permissions_changed is False
        connector.data_entities_processor.add_permission_to_record.assert_not_called()

# ---------------------------------------------------------------------------
# _sync_record_groups
# ---------------------------------------------------------------------------

class TestSyncRecordGroups:
    """Tests for syncing shared drives and personal drives."""

    async def test_syncs_shared_drives(self, connector):
        connector.drive_data_source.drives_list = AsyncMock(return_value={
            "drives": [_make_shared_drive("sd-1", "Shared 1")],
        })
        connector.drive_data_source.drives_get = AsyncMock(return_value={"id": "sd-1", "restrictions": {}})
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
        assert result.record.shared_with_me_record_group_ids == ["0S:reader@example.com"]
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


class TestTeamDriveItemPeople:
    async def test_my_drive_file_carries_owner_author_and_last_modifier(self, connector) -> None:
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        metadata = _make_file_metadata(
            owners=[{"displayName": "Alice", "emailAddress": "alice@example.com", "permissionId": "p-alice"}],
            parents=["folder-1"],
        )
        metadata["lastModifyingUser"] = {"displayName": "Bob", "emailAddress": "bob@example.com", "permissionId": "p-bob"}

        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        record = result.record
        assert [o.email for o in record.owners] == ["alice@example.com"]
        assert record.authored_by is not None
        assert record.authored_by.source_id == "p-alice"
        assert record.last_modified_by is not None
        assert record.last_modified_by.email == "bob@example.com"

    async def test_shared_drive_file_has_no_author(self, connector) -> None:
        connector.drive_data_source.permissions_list = AsyncMock(return_value={"permissions": []})
        metadata = _make_file_metadata(parents=["sd-1"], owners=[])
        metadata["driveId"] = "sd-1"
        metadata["lastModifyingUser"] = {"displayName": "Bob", "emailAddress": "bob@example.com", "permissionId": "p-bob"}

        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com", drive_id="sd-1", is_shared_drive=True
        )

        record = result.record
        assert record.authored_by is None
        assert record.owners == []
        assert record.last_modified_by is not None
        assert record.last_modified_by.source_id == "p-bob"


def _user_grant(pid: str, email: str, role: str, **extra) -> dict:
    return {"id": pid, "type": "user", "role": role, "emailAddress": email, **extra}


class TestTeamDriveItemSharedWith:
    """Who a file was shared with directly becomes record.shared_with; None when that cannot be told."""

    @staticmethod
    def _lists(by_file: dict[str, list[dict]]) -> tuple[Callable[..., Awaitable[dict]], list[str]]:
        calls: list[str] = []

        async def permissions_list(**params: object) -> dict:
            calls.append(params["fileId"])
            return {"permissions": by_file.get(params["fileId"], [])}

        return permissions_list, calls

    @staticmethod
    def _my_drive_file(**extra) -> dict:
        metadata = _make_file_metadata(
            owners=[{"displayName": "Alice", "emailAddress": "alice@example.com", "permissionId": "p-alice"}],
            parents=["folder-1"],
        )
        metadata["ownedByMe"] = True
        metadata.update(extra)
        return metadata

    async def test_a_my_drive_file_shared_with_one_person_names_them(self, connector) -> None:
        connector.drive_data_source.permissions_list, _ = self._lists({
            "file-1": [_user_grant("p-alice", "alice@example.com", "owner"), _user_grant("p-bob", "bob@example.com", "writer")],
            "folder-1": [_user_grant("p-alice", "alice@example.com", "owner")],
        })

        result = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert [(p.source_id, p.email) for p in result.record.shared_with] == [("p-bob", "bob@example.com")]

    async def test_a_grant_inherited_from_the_folder_is_not_shared_with(self, connector) -> None:
        bob = _user_grant("p-bob", "bob@example.com", "reader")
        connector.drive_data_source.permissions_list, _ = self._lists({
            "file-1": [_user_grant("p-alice", "alice@example.com", "owner"), bob],
            "folder-1": [_user_grant("p-alice", "alice@example.com", "owner"), bob],
        })

        result = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert result.record.shared_with == []

    async def test_a_shared_drive_item_uses_permission_details(self, connector) -> None:
        connector.drive_data_source.permissions_list, calls = self._lists({"file-1": [
            _user_grant("p-bob", "bob@example.com", "writer", permissionDetails=[{"permissionType": "file", "inherited": False}]),
            _user_grant("p-cat", "cat@example.com", "reader", permissionDetails=[{"permissionType": "member", "inherited": True}]),
        ]})
        metadata = _make_file_metadata(parents=["sd-1"], owners=[])
        metadata["driveId"] = "sd-1"

        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com", drive_id="sd-1", is_shared_drive=True
        )

        assert [p.email for p in result.record.shared_with] == ["bob@example.com"]
        assert calls == ["file-1"]

    async def test_a_transient_parent_read_fails_the_item_so_it_is_read_again(self, connector) -> None:
        from googleapiclient.errors import HttpError

        async def permissions_list(**params: object) -> dict:
            if params["fileId"] == "folder-1":
                raise HttpError(MagicMock(status=503), b"backend error")
            return {"permissions": [_user_grant("p-alice", "alice@example.com", "owner"), _user_grant("p-bob", "bob@example.com", "writer")]}

        connector.drive_data_source.permissions_list = permissions_list
        connector._sync_ledger = MagicMock()

        result = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert result is None
        connector._sync_ledger.fail_item.assert_called_once_with("file-1")

    async def test_a_rate_limited_parent_read_is_not_taken_as_a_refusal(self, connector) -> None:
        from googleapiclient.errors import HttpError

        body = b'{"error": {"errors": [{"reason": "userRateLimitExceeded"}], "code": 403}}'

        async def permissions_list(**params: object) -> dict:
            if params["fileId"] == "folder-1":
                raise HttpError(MagicMock(status=403), body)
            return {"permissions": [_user_grant("p-alice", "alice@example.com", "owner")]}

        connector.drive_data_source.permissions_list = permissions_list
        connector._sync_ledger = MagicMock()

        result = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert result is None
        connector._sync_ledger.fail_item.assert_called_once_with("file-1")

    async def test_an_unreadable_parent_leaves_shared_with_unknown(self, connector) -> None:
        from googleapiclient.errors import HttpError

        denied = b'{"error": {"errors": [{"reason": "insufficientFilePermissions"}], "code": 403}}'

        async def permissions_list(**params: object) -> dict:
            if params["fileId"] == "folder-1":
                raise HttpError(MagicMock(status=403), denied)
            return {"permissions": [_user_grant("p-alice", "alice@example.com", "owner"), _user_grant("p-bob", "bob@example.com", "writer")]}

        connector.drive_data_source.permissions_list = permissions_list

        result = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert result.record.shared_with is None

    async def test_a_parent_403_with_no_known_reason_fails_the_item_so_it_is_read_again(self, connector) -> None:
        from googleapiclient.errors import HttpError

        async def permissions_list(**params: object) -> dict:
            if params["fileId"] == "folder-1":
                raise HttpError(MagicMock(status=403), b"forbidden")
            return {"permissions": [_user_grant("p-alice", "alice@example.com", "owner")]}

        connector.drive_data_source.permissions_list = permissions_list
        connector._sync_ledger = MagicMock()

        result = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert result is None
        connector._sync_ledger.fail_item.assert_called_once_with("file-1")
        assert ("alice@example.com", "folder-1") not in connector._parent_grant_cache

    async def test_a_later_read_without_an_address_keeps_the_one_an_earlier_read_gave(self, connector) -> None:
        """Drive leaves an address out of some members' lists; the same permission id is the same person."""
        lists = {
            "file-1": [_user_grant("p-alice", "alice@example.com", "owner"), _user_grant("p-ext", "ext@other.test", "writer")],
            "folder-1": [_user_grant("p-alice", "alice@example.com", "owner")],
        }
        connector.drive_data_source.permissions_list, _ = self._lists(lists)
        first = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )
        lists["file-1"] = [_user_grant("p-alice", "alice@example.com", "owner"),
                           {"id": "p-ext", "type": "user", "role": "writer"}]
        metadata = self._my_drive_file(lastModifyingUser={"displayName": "Ext", "permissionId": "p-ext"})

        again = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert [p.email for p in first.record.shared_with] == ["ext@other.test"]
        assert [p.email for p in again.record.shared_with] == ["ext@other.test"]
        assert again.record.last_modified_by is not None and again.record.last_modified_by.email == "ext@other.test"

    async def test_a_hidden_owner_and_modifier_keep_their_stored_people_while_a_visible_sharee_is_linked(
        self, connector
    ) -> None:
        """A new sync knows no address yet: an id alone would be another node than the one stored by email."""
        connector.drive_data_source.permissions_list, _ = self._lists({
            "file-1": [{"id": "p-own", "type": "user", "role": "owner"},
                       _user_grant("p-bob", "bob@example.com", "writer")],
            "folder-1": [{"id": "p-own", "type": "user", "role": "owner"}],
        })
        connector._grant_emails_this_run = {}
        connector._shares_linked_this_run = {}
        hidden = {"displayName": "Own", "permissionId": "p-own"}
        metadata = self._my_drive_file(owners=[hidden], lastModifyingUser=hidden)

        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        record = result.record
        assert [p.email for p in record.shared_with] == ["bob@example.com"]
        assert record.owners == [] and record.last_modified_by is None and record.authored_by is None
        assert sorted(record.people_unknown) == ["AUTHORED_BY", "LAST_MODIFIED_BY", "OWNED_BY"]

    async def test_a_later_sync_whose_read_hides_an_address_keeps_the_stored_shares(self, connector) -> None:
        """A new sync knows no address yet: the share stays on the person stored by email."""
        connector.drive_data_source.permissions_list, _ = self._lists({
            "file-1": [_user_grant("p-alice", "alice@example.com", "owner"),
                       {"id": "p-ext", "type": "user", "role": "writer"}],
            "folder-1": [_user_grant("p-alice", "alice@example.com", "owner")],
        })
        connector._grant_emails_this_run = {}
        connector._shares_linked_this_run = {}

        result = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert result.record.shared_with is None

    async def test_in_a_limited_folder_every_grant_on_the_item_is_its_own(self, connector) -> None:
        bob = _user_grant("p-bob", "bob@example.com", "reader")
        connector.drive_data_source.permissions_list, calls = self._lists({
            "file-1": [_user_grant("p-alice", "alice@example.com", "owner"), bob],
            "folder-1": [_user_grant("p-alice", "alice@example.com", "owner"), bob],
        })

        result = await connector._process_drive_item(
            metadata=self._my_drive_file(inheritedPermissionsDisabled=True), user_id="u1",
            user_email="alice@example.com", drive_id="drive-1",
        )

        assert [p.email for p in result.record.shared_with] == ["bob@example.com"]
        assert "folder-1" not in calls

    async def test_a_sharee_who_cannot_see_the_parent_leaves_shared_with_unknown(self, connector) -> None:
        connector.drive_data_source.permissions_list, _ = self._lists({
            "file-1": [_user_grant("p-alice", "alice@example.com", "owner"), _user_grant("p-bob", "bob@example.com", "writer")],
        })
        metadata = self._my_drive_file(ownedByMe=False)
        metadata.pop("parents")

        result = await connector._process_drive_item(
            metadata=metadata, user_id="u2", user_email="bob@example.com", drive_id="drive-1"
        )

        assert result.record.shared_with is None

    async def test_an_owned_file_with_no_parent_counts_every_grant_as_direct(self, connector) -> None:
        connector.drive_data_source.permissions_list, _ = self._lists({
            "file-1": [_user_grant("p-alice", "alice@example.com", "owner"), _user_grant("p-bob", "bob@example.com", "writer")],
        })
        metadata = self._my_drive_file()
        metadata.pop("parents")

        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert [p.email for p in result.record.shared_with] == ["bob@example.com"]

    async def test_a_fallback_permission_read_leaves_shared_with_unknown(self, connector) -> None:
        connector._fetch_permissions = AsyncMock(return_value=(
            [Permission(email="alice@example.com", type=PermissionType.READ, entity_type=EntityType.USER)], True, [],
        ))

        result = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert result.record.shared_with is None

    async def test_parent_grants_are_read_once_per_folder_per_user(self, connector) -> None:
        connector.drive_data_source.permissions_list, calls = self._lists({
            "file-1": [_user_grant("p-alice", "alice@example.com", "owner")],
            "file-2": [_user_grant("p-alice", "alice@example.com", "owner")],
            "file-3": [_user_grant("p-alice", "alice@example.com", "owner")],
            "folder-1": [_user_grant("p-alice", "alice@example.com", "owner")],
        })

        for file_id, email in (("file-1", "alice@example.com"), ("file-2", "alice@example.com"), ("file-3", "dan@example.com")):
            await connector._process_drive_item(
                metadata=self._my_drive_file(id=file_id), user_id="u1", user_email=email, drive_id="drive-1"
            )

        assert calls.count("folder-1") == 2

    async def test_a_parent_list_that_repeats_its_page_token_leaves_shared_with_unknown(self, connector) -> None:
        async def permissions_list(**params: object) -> dict:
            if params["fileId"] == "folder-1":
                return {"permissions": [_user_grant("p-alice", "alice@example.com", "owner")], "nextPageToken": "again"}
            return {"permissions": [_user_grant("p-alice", "alice@example.com", "owner"),
                                    _user_grant("p-bob", "bob@example.com", "writer")]}

        connector.drive_data_source.permissions_list = permissions_list

        result = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert result.record.shared_with is None

    async def test_every_read_writes_the_shares_it_read(self, connector) -> None:
        """Not left to an earlier read this run: that read's write may not have persisted."""
        connector.drive_data_source.permissions_list, _ = self._lists({
            "file-1": [_user_grant("p-alice", "alice@example.com", "owner"), _user_grant("p-bob", "bob@example.com", "writer")],
            "folder-1": [_user_grant("p-alice", "alice@example.com", "owner")],
        })
        first = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        again = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert [p.email for p in first.record.shared_with] == ["bob@example.com"]
        assert [p.email for p in again.record.shared_with] == ["bob@example.com"]

    async def test_a_later_read_that_differs_in_the_same_run_is_still_applied(self, connector) -> None:
        lists = {
            "file-1": [_user_grant("p-alice", "alice@example.com", "owner"), _user_grant("p-bob", "bob@example.com", "writer")],
            "folder-1": [_user_grant("p-alice", "alice@example.com", "owner")],
        }
        connector.drive_data_source.permissions_list, _ = self._lists(lists)
        await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )
        lists["file-1"] = [_user_grant("p-alice", "alice@example.com", "owner")]  # Bob unshared since

        later = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert later.record.shared_with == []

    async def test_someone_a_drive_restriction_cuts_out_is_not_shared_with(self, connector) -> None:
        from app.connectors.sources.google.drive.team.drive_access import (
            DrivePermissionBatch,
        )

        record = MagicMock(shared_with=None)
        batch = DrivePermissionBatch(
            [], saw_permission_details=True, grant_details_seen=True,
            user_grants=[_user_grant("p-in", "in@example.com", "reader", permissionDetails=[{"inherited": False}]),
                         _user_grant("p-out", "out@other.test", "reader", permissionDetails=[{"inherited": False}])],
            allowed_email=lambda email: email.endswith("@example.com"),
        )

        await connector._set_people_from_grants(record, {"id": "sd-file", "driveId": "sd-1"}, batch, "u@example.com", None)

        assert [p.email for p in record.shared_with] == ["in@example.com"]

    async def test_a_metadata_only_grant_is_not_shared_with(self, connector) -> None:
        connector.drive_data_source.permissions_list, _ = self._lists({
            "file-1": [_user_grant("p-alice", "alice@example.com", "owner"),
                       _user_grant("p-bob", "bob@example.com", "reader", view="metadata")],
            "folder-1": [_user_grant("p-alice", "alice@example.com", "owner")],
        })

        result = await connector._process_drive_item(
            metadata=self._my_drive_file(), user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert result.record.shared_with == []

    async def test_a_manager_read_keeps_the_raw_grants_for_people(self, connector) -> None:
        from app.connectors.sources.google.drive.team.drive_access import (
            DrivePermissionBatch,
        )

        grants = [_user_grant("p-bob", "bob@example.com", "writer")]
        read = (DrivePermissionBatch([], saw_permission_details=True, user_grants=grants, grant_details_seen=True), False, [])
        connector._drive_member_email_list = AsyncMock(return_value=[])
        connector._read_as_drive_manager = AsyncMock(return_value=read)

        batch, _, _ = await connector._permissions_as_drive_manager("file-1", "sd-1", "user@example.com", {})

        assert batch.user_grants == grants
        assert batch.grant_details_seen is True

    async def test_a_reader_who_can_see_the_folder_still_leaves_my_drive_shares_unknown(self, connector) -> None:
        connector.drive_data_source.permissions_list, calls = self._lists({
            "file-1": [_user_grant("p-alice", "alice@example.com", "owner"), _user_grant("p-bob", "bob@example.com", "writer")],
            "folder-1": [_user_grant("p-alice", "alice@example.com", "owner")],
        })

        result = await connector._process_drive_item(
            metadata=self._my_drive_file(ownedByMe=False), user_id="u2", user_email="bob@example.com", drive_id="drive-1"
        )

        assert result.record.shared_with is None
        assert "folder-1" not in calls

    async def test_a_hidden_owner_email_is_filled_after_permissions_are_read(self, connector) -> None:
        connector.drive_data_source.permissions_list, _ = self._lists({
            "file-1": [_user_grant("01448", "test@ext.test", "owner")],
            "folder-1": [],
        })
        metadata = self._my_drive_file(owners=[{"displayName": "test", "permissionId": "01448"}], ownedByMe=False)

        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="alice@example.com", drive_id="drive-1"
        )

        assert result.record.owners[0].email == "test@ext.test"
        assert result.record.authored_by is not None and result.record.authored_by.email == "test@ext.test"


async def test_a_reindex_reads_folder_sharing_afresh(connector) -> None:
    connector._parent_grant_cache = {("alice@example.com", "folder-1"): []}

    await connector.reindex_records([])

    assert connector._parent_grant_cache == {}


def _drives(state: dict) -> dict:
    import json

    stored = state.get("foreign_shared_drives") or {}
    assert all(not isinstance(v, dict) for v in stored.values()), "a sync point holds no nested map"
    return json.loads(stored.get("drives") or "{}")


class _Everyone(frozenset):
    """Every address counts as a user the deployment syncs, unless a test narrows it."""

    def __contains__(self, _item: object) -> bool:
        return True


_EVERYONE = _Everyone()


class TestForeignSharedDrive:
    """A shared drive a user reaches that the domain's admin listing does not have (another organization's)."""

    FOREIGN = {"id": "0APG", "name": "Untitled shared drive"}

    @staticmethod
    def _store(connector) -> dict:
        state: dict = {}

        async def read(key: str) -> dict | None:
            return state.get(key)

        async def update(key: str, value: dict) -> None:
            state[key] = value

        connector.drive_delta_sync_point.read_sync_point = AsyncMock(side_effect=read)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock(side_effect=update)
        connector._shared_drives_listed = True
        connector._listed_shared_drive_ids = {"own-drive"}
        connector._active_member_emails = AsyncMock(return_value=_EVERYONE)
        # The admin cannot open it: another organization's drive.
        connector._shared_drive_exists = AsyncMock(return_value=False)
        # No records stored for it yet, so a leaver has no grants to remove.
        connector.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=None)
        return state

    @staticmethod
    def _user(email: str) -> AppUser:
        return AppUser(app_name=Connectors.GOOGLE_DRIVE_WORKSPACE, connector_id="conn-1", source_user_id=email,
                       org_id="org-123", email=email, full_name=email, is_active=True)

    @staticmethod
    def _last_group_write(connector) -> tuple:
        (pairs,), _ = connector.data_entities_processor.on_new_record_groups.await_args
        group, permissions = pairs[0]
        return group, sorted(p.email for p in permissions)

    @staticmethod
    def _next_run(connector) -> None:
        connector._foreign_drives_written = set()
        connector._foreign_drive_confirmed = {}
        connector._leaver_cleanup_failed = set()
        connector._foreign_group_reach = {}

    def test_a_member_only_grant_on_a_shared_drive_item_is_not_stored_on_the_item(self) -> None:
        """Why the files were unreachable: their members' access is meant to come from the drive's group."""
        from app.connectors.sources.google.drive.team.drive_access import (
            grants_to_store,
        )

        member = Permission(email="v@example.com", type=PermissionType.READ, entity_type=EntityType.USER)

        assert grants_to_store([member], [], saw_permission_details=True, inherited_permissions_disabled=False,
                               is_drive=False) == []

    async def test_a_drive_only_the_user_lists_gets_its_name_and_the_user(self, connector) -> None:
        self._store(connector)

        await connector._note_foreign_drives_reached(self._user("v@example.com"), [self.FOREIGN, {"id": "own-drive"}])

        group, members = self._last_group_write(connector)
        assert group.external_group_id == "0APG"
        assert group.name == "Untitled shared drive"
        assert group.group_type == RecordGroupType.DRIVE
        assert members == ["v@example.com"]
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    async def test_a_drive_the_admin_can_still_open_is_not_foreign(self, connector) -> None:
        """A short admin listing leaves one of this Workspace's drives out; its own grants must stay."""
        state = self._store(connector)
        connector._shared_drive_exists = AsyncMock(return_value=True)

        await connector._note_foreign_drives_reached(self._user("v@example.com"), [self.FOREIGN])

        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()
        assert _drives(state) == {}

    async def test_a_drive_whose_owner_cannot_be_told_is_left_alone_this_run(self, connector) -> None:
        state = self._store(connector)
        connector._shared_drive_exists = AsyncMock(return_value=None)

        await connector._note_foreign_drives_reached(self._user("v@example.com"), [self.FOREIGN])

        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()
        assert _drives(state) == {}

    async def test_a_user_who_leaves_loses_only_their_grant(self, connector) -> None:
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        await connector._note_foreign_drives_reached(self._user("b@example.com"), [self.FOREIGN])

        await connector._note_foreign_drives_reached(self._user("a@example.com"), [])

        assert self._last_group_write(connector)[1] == ["b@example.com"]

    async def test_the_last_member_leaving_revokes_the_group_and_deletes_nothing(self, connector) -> None:
        state = self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])

        with patch(
            "app.connectors.sources.google.drive.team.connector.delete_shared_drive_records", new=AsyncMock()
        ) as delete:
            await connector._note_foreign_drives_reached(self._user("a@example.com"), [])

        assert self._last_group_write(connector)[1] == []
        delete.assert_not_awaited()
        assert _drives(state)["0APG"] == {"name": "Untitled shared drive", "members": []}

    async def test_a_member_who_rejoins_gets_the_group_back(self, connector) -> None:
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [])

        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])

        assert self._last_group_write(connector)[1] == ["a@example.com"]

    async def test_a_member_who_is_no_longer_synced_is_dropped(self, connector) -> None:
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("gone@example.com"), [self.FOREIGN])
        connector._active_member_emails = AsyncMock(return_value={"b@example.com"})

        await connector._note_foreign_drives_reached(self._user("b@example.com"), [self.FOREIGN])

        assert self._last_group_write(connector)[1] == ["b@example.com"]

    async def test_the_last_eligible_member_leaving_the_deployment_is_revoked_once_a_run(self, connector) -> None:
        state = self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        connector._active_member_emails = AsyncMock(return_value=set())

        await connector._prune_foreign_drives()

        assert self._last_group_write(connector)[1] == []
        assert _drives(state)["0APG"]["members"] == []

    async def test_a_run_with_no_user_to_sync_still_prunes_the_foreign_drives(self, connector) -> None:
        connector._get_users_to_sync = AsyncMock(return_value=[])
        connector._prune_foreign_drives = AsyncMock()

        await connector._process_users_in_batches([])

        connector._prune_foreign_drives.assert_awaited_once()

    async def test_a_cut_user_listing_prunes_nobody(self, connector) -> None:
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("z@example.com"), [self.FOREIGN])
        connector._active_member_emails = AsyncMock(return_value={"b@example.com"})
        connector._listing_cut = True

        await connector._note_foreign_drives_reached(self._user("b@example.com"), [self.FOREIGN])

        assert self._last_group_write(connector)[1] == ["b@example.com", "z@example.com"]

    async def test_the_run_prune_keeps_everyone_on_a_cut_user_listing(self, connector) -> None:
        state = self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        connector._active_member_emails = AsyncMock(return_value=set())
        connector._listing_cut = True
        connector.data_entities_processor.on_new_record_groups.reset_mock()

        await connector._prune_foreign_drives()

        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()
        assert _drives(state)["0APG"]["members"] == ["a@example.com"]

    async def test_a_join_whose_group_write_fails_is_stored_and_written_again(self, connector) -> None:
        """The membership is stored before the write; the failed write grants nobody, and is retried."""
        state = self._store(connector)
        connector._sync_ledger = MagicMock()
        connector.data_entities_processor.on_new_record_groups = AsyncMock(side_effect=RuntimeError("store down"))

        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])

        connector._sync_ledger.note_incomplete.assert_called_once()
        assert _drives(state)["0APG"]["members"] == ["a@example.com"]
        assert not connector._reaches_foreign_drive("a@example.com", "0APG")
        connector.data_entities_processor.on_new_record_groups = AsyncMock()
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        assert self._last_group_write(connector)[1] == ["a@example.com"]
        assert connector._reaches_foreign_drive("a@example.com", "0APG")

    async def test_a_leave_whose_group_write_fails_is_stored_as_revoked_and_written_again(self, connector) -> None:
        """No later rewrite can start from a membership that still holds the leaver."""
        state = self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        await connector._note_foreign_drives_reached(self._user("b@example.com"), [self.FOREIGN])
        connector._sync_ledger = MagicMock()
        connector.data_entities_processor.on_new_record_groups = AsyncMock(side_effect=RuntimeError("store down"))

        await connector._note_foreign_drives_reached(self._user("a@example.com"), [])

        assert _drives(state)["0APG"]["members"] == ["b@example.com"]
        assert _drives(state)["0APG"]["leavers"] == ["a@example.com"]
        connector.data_entities_processor.on_new_record_groups = AsyncMock()
        await connector._note_foreign_drives_reached(self._user("b@example.com"), [self.FOREIGN])
        assert self._last_group_write(connector)[1] == ["b@example.com"]
        assert _drives(state)["0APG"].get("leavers", []) == []

    async def test_one_drives_failed_write_leaves_the_others_written(self, connector) -> None:
        state = self._store(connector)
        other = {"id": "0BXX", "name": "Partner drive"}
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN, other])
        self._next_run(connector)

        async def write(pairs: list) -> None:
            if pairs[0][0].external_group_id == "0BXX":
                raise RuntimeError("store down")

        connector._sync_ledger = MagicMock()
        connector.data_entities_processor.on_new_record_groups = AsyncMock(side_effect=write)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [])

        assert _drives(state)["0APG"]["members"] == [] and _drives(state)["0BXX"]["members"] == []
        assert "0APG" in connector._foreign_drives_written and "0BXX" not in connector._foreign_drives_written

    async def test_concurrent_user_syncs_keep_both_members(self, connector) -> None:
        state = self._store(connector)
        read = connector.drive_delta_sync_point.read_sync_point.side_effect

        async def slow_read(key: str) -> dict | None:
            value = await read(key)
            await asyncio.sleep(0)
            return value

        connector.drive_delta_sync_point.read_sync_point = AsyncMock(side_effect=slow_read)

        await asyncio.gather(
            connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN]),
            connector._note_foreign_drives_reached(self._user("b@example.com"), [self.FOREIGN]),
        )

        assert sorted(_drives(state)["0APG"]["members"]) == ["a@example.com", "b@example.com"]

    async def test_an_admin_listed_drive_is_never_treated_as_foreign(self, connector) -> None:
        state = self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        connector._listed_shared_drive_ids = {"own-drive", "0APG"}
        connector.data_entities_processor.on_new_record_groups.reset_mock()
        self._next_run(connector)

        await connector._note_foreign_drives_reached(self._user("a@example.com"), [])

        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()
        assert _drives(state) == {}

    async def test_a_reached_drive_is_written_once_a_run_so_a_full_sync_keeps_it(self, connector) -> None:
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        connector.data_entities_processor.on_new_record_groups.reset_mock()
        connector.drive_delta_sync_point.update_sync_point.reset_mock()

        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

        self._next_run(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])

        assert self._last_group_write(connector)[1] == ["a@example.com"]
        connector.drive_delta_sync_point.update_sync_point.assert_not_awaited()

    async def test_a_leaver_loses_their_own_grants_on_the_drive_except_files_shared_with_them(self, connector) -> None:
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        await connector._note_foreign_drives_reached(self._user("b@example.com"), [self.FOREIGN])
        groups = {"0APG": MagicMock(id="group-d"), "0S:a@example.com": MagicMock(id="swm-a")}
        records = [MagicMock(id="rec-1"), MagicMock(id="rec-2")]
        dep = connector.data_entities_processor
        dep.get_record_group_by_external_id = AsyncMock(side_effect=lambda _c, ext: groups.get(ext))
        dep.get_records_by_status = AsyncMock(side_effect=[records, []])
        dep.record_in_group = AsyncMock(side_effect=lambda record_id, group_id: (record_id, group_id) == ("rec-2", "swm-a"))
        dep.delete_permission_from_record = AsyncMock()

        await connector._note_foreign_drives_reached(self._user("a@example.com"), [])

        assert self._last_group_write(connector)[1] == ["b@example.com"]
        dep.delete_permission_from_record.assert_awaited_once_with("rec-1", "a@example.com", connector.connector_id)

    async def test_a_leaver_loses_their_grant_on_a_trashed_file_too(self, connector) -> None:
        """Restored, a trashed file would come back still granting the leaver."""
        from app.services.graph_db.common.record_visibility import RecordVisibility

        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        dep = connector.data_entities_processor
        dep.get_record_group_by_external_id = AsyncMock(side_effect=lambda _c, ext: MagicMock(id="group-d") if ext == "0APG" else None)
        dep.get_records_by_status = AsyncMock(side_effect=[[MagicMock(id="trashed-1")], []])
        dep.delete_permission_from_record = AsyncMock()

        await connector._note_foreign_drives_reached(self._user("a@example.com"), [])

        assert dep.get_records_by_status.await_args.kwargs["visibility"] is RecordVisibility.ALL
        dep.delete_permission_from_record.assert_awaited_once_with("trashed-1", "a@example.com", connector.connector_id)

    async def test_a_leaver_whose_grants_cannot_be_cleared_stays_pending(self, connector) -> None:
        state = self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        connector._sync_ledger = MagicMock()
        dep = connector.data_entities_processor
        dep.get_record_group_by_external_id = AsyncMock(side_effect=RuntimeError("graph down"))

        await connector._note_foreign_drives_reached(self._user("a@example.com"), [])

        assert _drives(state)["0APG"]["members"] == []
        assert _drives(state)["0APG"]["leavers"] == ["a@example.com"]
        connector._sync_ledger.note_incomplete.assert_called_once()

    async def test_a_stored_drive_the_admin_can_open_is_dropped_without_a_write(self, connector) -> None:
        state = self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        self._next_run(connector)
        connector._shared_drive_exists = AsyncMock(return_value=True)
        connector.data_entities_processor.on_new_record_groups.reset_mock()

        await connector._note_foreign_drives_reached(self._user("b@example.com"), [])

        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()
        assert _drives(state) == {}

    async def test_a_stored_drive_whose_owner_cannot_be_told_is_left_as_stored(self, connector) -> None:
        state = self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        self._next_run(connector)
        connector._shared_drive_exists = AsyncMock(return_value=None)
        connector.data_entities_processor.on_new_record_groups.reset_mock()

        await connector._note_foreign_drives_reached(self._user("a@example.com"), [])

        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()
        assert _drives(state)["0APG"]["members"] == ["a@example.com"]

    async def test_a_limited_item_keeps_the_viewers_own_grant(self, connector) -> None:
        """A limited item inherits nothing from the drive: the reader's own grant is all the access it has."""
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("viewer@example.com"), [self.FOREIGN])
        connector._fetch_permissions = AsyncMock(return_value=(
            [Permission(email="viewer@example.com", type=PermissionType.READ, entity_type=EntityType.USER)], True, [],
        ))
        existing = MagicMock(id="rec-1", external_record_group_id="0APG", parent_external_record_id=None)
        dep = connector.data_entities_processor
        dep.get_record_by_external_id = AsyncMock(return_value=existing)
        dep.get_roles_on_record = AsyncMock(return_value=({}, {}))
        dep.add_permission_to_record = AsyncMock()
        dep.delete_permission_from_record = AsyncMock()
        metadata = _make_file_metadata(parents=["0APG"], owners=[])
        metadata["driveId"] = "0APG"
        metadata["inheritedPermissionsDisabled"] = True

        await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="viewer@example.com", drive_id="0APG", is_shared_drive=True
        )

        dep.delete_permission_from_record.assert_not_awaited()

    def _viewer_item(self, connector, *, parents: list[str], records: dict) -> dict:
        connector._fetch_permissions = AsyncMock(return_value=(
            [Permission(email="viewer@example.com", type=PermissionType.READ, entity_type=EntityType.USER)], True, [],
        ))
        dep = connector.data_entities_processor
        dep.get_record_by_external_id = AsyncMock(side_effect=lambda connector_id=None, external_record_id=None, **_: records.get(external_record_id))
        dep.get_roles_on_record = AsyncMock(return_value=({}, {}))
        dep.add_permission_to_record = AsyncMock()
        dep.delete_permission_from_record = AsyncMock()
        metadata = _make_file_metadata(parents=parents, owners=[])
        metadata["driveId"] = "0APG"
        return metadata

    async def test_below_a_limited_folder_the_viewer_keeps_their_own_grant(self, connector) -> None:
        """A limited folder above cuts the drive's group off: their own grant is all the access they have."""
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("viewer@example.com"), [self.FOREIGN])
        limited = MagicMock(id="rec-L", parent_external_record_id=None)
        item = MagicMock(id="rec-1", external_record_group_id="0APG", parent_external_record_id="folder-L")
        metadata = self._viewer_item(connector, parents=["folder-L"], records={"file-1": item, "folder-L": limited})
        connector.data_entities_processor.inheritance_when_unreadable = AsyncMock(
            side_effect=lambda _collection, record_id: record_id != "rec-L"
        )

        await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="viewer@example.com", drive_id="0APG", is_shared_drive=True
        )

        connector.data_entities_processor.delete_permission_from_record.assert_not_awaited()
        connector.data_entities_processor.add_permission_to_record.assert_awaited_once()

    async def test_below_a_folder_not_stored_yet_the_viewer_keeps_their_own_grant(self, connector) -> None:
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("viewer@example.com"), [self.FOREIGN])
        item = MagicMock(id="rec-1", external_record_group_id="0APG", parent_external_record_id="folder-new")
        metadata = self._viewer_item(connector, parents=["folder-new"], records={"file-1": item})
        connector.data_entities_processor.inheritance_when_unreadable = AsyncMock(return_value=True)

        await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="viewer@example.com", drive_id="0APG", is_shared_drive=True
        )

        connector.data_entities_processor.delete_permission_from_record.assert_not_awaited()

    async def test_below_folders_that_all_inherit_the_viewer_reads_through_the_drive(self, connector) -> None:
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("viewer@example.com"), [self.FOREIGN])
        folder = MagicMock(id="rec-F", parent_external_record_id=None)
        item = MagicMock(id="rec-1", external_record_group_id="0APG", parent_external_record_id="folder-F")
        metadata = self._viewer_item(connector, parents=["folder-F"], records={"file-1": item, "folder-F": folder})
        connector.data_entities_processor.inheritance_when_unreadable = AsyncMock(return_value=True)

        await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="viewer@example.com", drive_id="0APG", is_shared_drive=True
        )

        connector.data_entities_processor.delete_permission_from_record.assert_awaited_once_with(
            "rec-1", "viewer@example.com", connector.connector_id
        )

    async def test_a_viewer_whose_group_grant_was_not_written_keeps_their_own_grant(self, connector) -> None:
        self._store(connector)
        connector.data_entities_processor.on_new_record_groups = AsyncMock(side_effect=RuntimeError("graph down"))
        connector._sync_ledger = MagicMock()
        await connector._note_foreign_drives_reached(self._user("viewer@example.com"), [self.FOREIGN])
        item = MagicMock(id="rec-1", external_record_group_id="0APG", parent_external_record_id=None)
        metadata = self._viewer_item(connector, parents=["0APG"], records={"file-1": item})

        await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="viewer@example.com", drive_id="0APG", is_shared_drive=True
        )

        connector.data_entities_processor.delete_permission_from_record.assert_not_awaited()
        connector.data_entities_processor.add_permission_to_record.assert_awaited_once()

    async def test_a_revoked_leaver_is_not_written_back_while_their_file_grants_wait(self, connector) -> None:
        """The group revocation is stored once written; a failed file cleanup is retried, not undone."""
        state = self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        await connector._note_foreign_drives_reached(self._user("b@example.com"), [self.FOREIGN])
        connector._sync_ledger = MagicMock()
        dep = connector.data_entities_processor
        dep.get_record_group_by_external_id = AsyncMock(side_effect=RuntimeError("graph down"))

        await connector._note_foreign_drives_reached(self._user("a@example.com"), [])
        assert _drives(state)["0APG"]["members"] == ["b@example.com"]
        assert _drives(state)["0APG"]["leavers"] == ["a@example.com"]

        self._next_run(connector)
        dep.get_record_group_by_external_id = AsyncMock(return_value=None)
        await connector._note_foreign_drives_reached(self._user("b@example.com"), [self.FOREIGN])

        assert self._last_group_write(connector)[1] == ["b@example.com"]
        assert _drives(state)["0APG"]["members"] == ["b@example.com"]
        assert _drives(state)["0APG"].get("leavers", []) == []

    async def test_the_records_of_a_drive_nobody_reaches_are_kept_from_the_cleanup(self, connector) -> None:
        state = self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [])
        assert _drives(state)["0APG"]["members"] == []
        dep = connector.data_entities_processor
        dep.get_record_group_by_external_id = AsyncMock(return_value=MagicMock(id="group-d"))
        dep.get_records_by_status = AsyncMock(side_effect=[[MagicMock(id="rec-1", external_record_id="file-1")]])
        dep.note_kept_records = MagicMock()
        connector._sync_ledger = MagicMock()

        kept = await connector._keep_memberless_foreign_drive_records(reconcile=True, full_sync=True)

        assert kept == 1
        connector._sync_ledger.see.assert_called_once_with("file-1")
        dep.note_kept_records.assert_called_once_with(connector.connector_id, ["rec-1"])

    async def test_the_records_of_a_drive_with_members_are_left_to_their_reads(self, connector) -> None:
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        dep = connector.data_entities_processor
        dep.get_records_by_status = AsyncMock()
        connector._sync_ledger = MagicMock()

        assert await connector._keep_memberless_foreign_drive_records(reconcile=True, full_sync=True) == 0
        dep.get_records_by_status.assert_not_awaited()

    async def test_a_viewer_whose_listing_hides_the_parent_keeps_their_own_grant(self, connector) -> None:
        """Drive leaves `parents` out when the reader cannot see the folder: that is not the drive's root."""
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("viewer@example.com"), [self.FOREIGN])
        item = MagicMock(id="rec-1", external_record_group_id="0APG", parent_external_record_id=None)
        metadata = self._viewer_item(connector, parents=["x"], records={"file-1": item})
        del metadata["parents"]

        await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="viewer@example.com", drive_id="0APG", is_shared_drive=True
        )

        connector.data_entities_processor.delete_permission_from_record.assert_not_awaited()

    async def test_a_viewer_keeps_their_own_grant_until_the_item_inherits(self, connector) -> None:
        """Its inheritance is written with the record, later: until then their own grant is their only access."""
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("viewer@example.com"), [self.FOREIGN])
        item = MagicMock(id="rec-1", external_record_group_id="0APG", parent_external_record_id=None)
        metadata = self._viewer_item(connector, parents=["0APG"], records={"file-1": item})
        connector.data_entities_processor.inheritance_when_unreadable = AsyncMock(return_value=False)

        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="viewer@example.com", drive_id="0APG", is_shared_drive=True
        )

        assert result.record.inherit_permissions is True
        connector.data_entities_processor.delete_permission_from_record.assert_not_awaited()

    async def test_a_folder_that_stops_inheriting_clears_what_the_group_was_known_to_reach(self, connector) -> None:
        connector._foreign_group_reach = {"folder-F": True}
        existing = MagicMock(id="rec-F", external_record_group_id="0APG", parent_external_record_id=None)
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        connector._fetch_permissions = AsyncMock(return_value=([], True, []))
        connector.data_entities_processor.stop_inheriting_permissions = AsyncMock()
        metadata = _make_file_metadata(parents=["0APG"], owners=[])
        metadata["driveId"] = "0APG"
        metadata["inheritedPermissionsDisabled"] = True

        await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="viewer@example.com", drive_id="0APG", is_shared_drive=True
        )

        connector.data_entities_processor.stop_inheriting_permissions.assert_awaited_once()
        assert connector._foreign_group_reach == {}

    async def test_a_failed_read_of_who_is_active_prunes_nobody(self, connector) -> None:
        state = self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        await connector._note_foreign_drives_reached(self._user("b@example.com"), [self.FOREIGN])
        connector._active_member_emails = AsyncMock(side_effect=RuntimeError("graph down"))
        connector.data_entities_processor.on_new_record_groups.reset_mock()

        with pytest.raises(RuntimeError):
            await connector._reconcile_foreign_drives("b@example.com", {"0APG": "Untitled shared drive"})

        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()
        assert _drives(state)["0APG"]["members"] == ["a@example.com", "b@example.com"]

    async def test_a_suspended_member_is_pruned_even_on_a_cut_listing(self, connector) -> None:
        state = self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        await connector._note_foreign_drives_reached(self._user("b@example.com"), [self.FOREIGN])
        self._next_run(connector)
        connector._listing_cut = True
        connector._is_inactive_email = MagicMock(side_effect=lambda e: e == "a@example.com")

        await connector._note_foreign_drives_reached(self._user("b@example.com"), [self.FOREIGN])

        assert self._last_group_write(connector)[1] == ["b@example.com"]
        assert _drives(state)["0APG"]["members"] == ["b@example.com"]

    @pytest.mark.parametrize(("answer", "opens"), [
        ({"mimeType": "application/vnd.google-apps.folder", "capabilities": {"canListChildren": False}}, False),
        ({"mimeType": "application/vnd.google-apps.folder", "capabilities": {"canListChildren": True}}, True),
        ({"mimeType": "application/pdf", "capabilities": {}}, True),
        ({"mimeType": "application/pdf", "trashed": True}, True),
    ])
    async def test_a_leaver_opens_a_folder_only_when_they_can_list_it(self, connector, answer, opens) -> None:
        """A limited folder shows its name to people it cuts out: that is not access to what is in it."""
        source = MagicMock()
        source.files_get = AsyncMock(return_value={"id": "f", **answer})
        connector._build_user_drive_data_source = AsyncMock(return_value=source)

        result = await connector._leaver_still_opens(MagicMock(external_record_id="f"), self._user("a@example.com"), {})

        assert result is opens
        assert "capabilities/canListChildren" in source.files_get.await_args.kwargs["fields"]

    async def test_a_leaver_the_file_is_gone_for_does_not_open_it(self, connector) -> None:
        from googleapiclient.errors import HttpError

        source = MagicMock()
        source.files_get = AsyncMock(side_effect=HttpError(MagicMock(status=404), b"not found"))
        connector._build_user_drive_data_source = AsyncMock(return_value=source)

        assert await connector._leaver_still_opens(MagicMock(external_record_id="f"), self._user("a@example.com"), {}) is False

    async def test_a_leaver_who_still_opens_a_file_keeps_their_grant_on_it(self, connector) -> None:
        """A share of their own on it (below a limited folder, say) outlasts their leaving the drive."""
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("a@example.com"), [self.FOREIGN])
        connector.synced_users = [self._user("a@example.com")]
        dep = connector.data_entities_processor
        dep.get_record_group_by_external_id = AsyncMock(side_effect=lambda _c, ext: MagicMock(id="group-d") if ext == "0APG" else None)
        dep.get_records_by_status = AsyncMock(side_effect=[[MagicMock(id="shared-1"), MagicMock(id="gone-1")], []])
        dep.delete_permission_from_record = AsyncMock()
        connector._leaver_still_opens = AsyncMock(side_effect=lambda record, *_: record.id == "shared-1")

        await connector._note_foreign_drives_reached(self._user("a@example.com"), [])

        dep.delete_permission_from_record.assert_awaited_once_with("gone-1", "a@example.com", connector.connector_id)

    async def test_a_viewer_member_who_cannot_read_sharing_gets_no_grant_of_their_own(self, connector) -> None:
        """They read the file through the foreign drive's group; an own grant would outlast their leaving."""
        self._store(connector)
        await connector._note_foreign_drives_reached(self._user("viewer@example.com"), [self.FOREIGN])
        connector._fetch_permissions = AsyncMock(return_value=(
            [Permission(email="viewer@example.com", type=PermissionType.READ, entity_type=EntityType.USER)], True, [],
        ))
        existing = MagicMock(id="rec-1", external_record_group_id="0APG", parent_external_record_id=None)
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        connector.data_entities_processor.add_permission_to_record = AsyncMock()
        connector.data_entities_processor.delete_permission_from_record = AsyncMock()
        metadata = _make_file_metadata(parents=["0APG"], owners=[])
        metadata["driveId"] = "0APG"

        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="viewer@example.com", drive_id="0APG", is_shared_drive=True
        )

        assert result.record.inherit_permissions is True
        connector.data_entities_processor.add_permission_to_record.assert_not_awaited()
        connector.data_entities_processor.delete_permission_from_record.assert_awaited_once_with(
            "rec-1", "viewer@example.com", connector.connector_id
        )

