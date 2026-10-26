"""The Dropbox Team connector's shared folders, streaming candidates and listing (fix H).

Found in a live run of PR #3880: a shared folder was synced once per member, under
each member's path, became an empty group named by its namespace id, and every
item under it got a grant for whichever member synced it last (D-4, I-2).
"""

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dropbox.files import FileMetadata, FolderMetadata, FolderSharingInfo

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.connectors.sources.dropbox.shared_folders import SharedFolder
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.dropbox.dropbox_ import DropboxResponse

CONNECTOR_ID = "dbx-conn-1"


@pytest.fixture
def connector() -> DropboxConnector:
    with patch("app.connectors.sources.dropbox.connector.DropboxApp"):
        conn = DropboxConnector(
            logger=logging.getLogger("dropbox-shared-folder-sync"),
            data_entities_processor=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=AsyncMock(),
            connector_id=CONNECTOR_ID,
            scope="team",
            created_by="admin",
        )
    conn.sync_filters = FilterCollection()
    conn.indexing_filters = FilterCollection()
    conn.data_source = AsyncMock()
    processor = conn.data_entities_processor
    processor.org_id = "org-1"
    processor.on_new_record_groups = AsyncMock()
    processor.get_record_group_by_external_id = AsyncMock(return_value=None)
    processor.get_users_with_permission_to_node = AsyncMock(return_value=[])
    processor.get_app_user_by_email = AsyncMock(return_value=None)
    processor.get_groups_with_access_to_record = AsyncMock(return_value=[])
    return conn


def _folder(folder_id: str = "sf-1", name: str = "Korean files", *, parent: str | None = None,
            inherits: bool = False, team: bool = False) -> SharedFolder:
    return SharedFolder(folder_id=folder_id, name=name, parent_id=parent, inherits=inherits,
                        is_team_folder=team, access={"dbmid:a": "owner"})


GRANT = Permission(external_id="g:1", type=PermissionType.READ, entity_type=EntityType.GROUP)


class TestSharedFolderGroups:
    @pytest.mark.asyncio
    async def test_a_new_shared_folder_is_a_group_under_its_own_name_granted_to_its_members(self, connector) -> None:
        connector._shared_folder_members = AsyncMock(return_value=[GRANT])

        await connector._sync_shared_folder_groups({"sf-1": _folder()})

        (group, grants), = connector.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert (group.name, group.external_group_id, group.inherit_permissions) == ("Korean files", "sf-1", False)
        assert grants == [GRANT]
        connector._shared_folder_members.assert_awaited_once_with("sf-1", "dbmid:a")

    @pytest.mark.asyncio
    async def test_a_member_removed_at_the_source_loses_the_grant_on_a_full_sync(self, connector) -> None:
        stored = MagicMock()
        stored.name = "Korean files"
        connector.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=stored)
        owner = SimpleNamespace(user=SimpleNamespace(email="a@x.com", account_id="dbid:a"),
                                access_type=SimpleNamespace(_tag="owner"), is_inherited=False)
        connector.data_source.sharing_list_folder_members = AsyncMock(return_value=DropboxResponse(
            success=True, data=SimpleNamespace(users=[owner], groups=[], invitees=[], cursor=None),
        ))

        await connector._sync_shared_folder_groups({"sf-1": _folder()})

        connector.data_source.sharing_list_folder_members.assert_awaited_once_with(
            shared_folder_id="sf-1", team_member_id="dbmid:a",
        )
        (_, grants), = connector.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert [g.email for g in grants] == ["a@x.com"], "the stored grants are replaced, so the leaver's is gone"

    @pytest.mark.asyncio
    async def test_a_placeholder_named_by_its_id_or_a_renamed_folder_rereads_its_members(self, connector) -> None:
        stored = MagicMock()
        stored.name = "sf-1"
        connector.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=stored)
        connector._shared_folder_members = AsyncMock(return_value=[GRANT])

        await connector._sync_shared_folder_groups({"sf-1": _folder()})

        connector._shared_folder_members.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_nested_inheriting_folder_hangs_under_its_parent(self, connector) -> None:
        connector._shared_folder_members = AsyncMock(return_value=[])

        await connector._sync_shared_folder_groups({
            "child": _folder("child", "Child", parent="top", inherits=True), "top": _folder("top", "Top"),
        })

        written = [call.args[0][0][0] for call in connector.data_entities_processor.on_new_record_groups.await_args_list]
        assert [g.external_group_id for g in written] == ["top", "child"]
        assert (written[1].parent_external_group_id, written[1].inherit_permissions) == ("top", True)

    @pytest.mark.asyncio
    async def test_team_folders_are_left_to_the_team_folder_sync(self, connector) -> None:
        await connector._sync_shared_folder_groups({"t": _folder("t", "Team", team=True)})
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_sharing_event_rereads_the_folder_and_its_members(self, connector) -> None:
        metadata = SimpleNamespace(shared_folder_id="sf-1", name="Renamed", access_type=SimpleNamespace(_tag="owner"),
                                   parent_shared_folder_id=None, access_inheritance=SimpleNamespace(_tag="inherit"),
                                   is_team_folder=False)
        connector.data_source.sharing_get_folder_metadata = AsyncMock(return_value=DropboxResponse(success=True, data=metadata))
        stored = MagicMock()
        stored.name = "Renamed"
        connector.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=stored)
        connector._shared_folder_members = AsyncMock(return_value=[GRANT])

        await connector._refresh_shared_folder("sf-1", "dbmid:a")

        connector._shared_folder_members.assert_awaited_once_with("sf-1", "dbmid:a")

    @pytest.mark.asyncio
    async def test_a_relinquish_event_rereads_the_folder_as_its_syncer_not_the_leaver(self, connector) -> None:
        connector._shared_folder_plan = {"sf-1": _folder()}
        metadata = SimpleNamespace(shared_folder_id="sf-1", name="Korean files", access_type=SimpleNamespace(_tag="owner"),
                                   parent_shared_folder_id=None, access_inheritance=SimpleNamespace(_tag="inherit"),
                                   is_team_folder=False)
        connector.data_source.sharing_get_folder_metadata = AsyncMock(return_value=DropboxResponse(success=True, data=metadata))
        connector._shared_folder_members = AsyncMock(return_value=[GRANT])
        event = MagicMock()
        event.event_type._tag = "shared_content_relinquish_membership"
        event.context.is_team_member.return_value = True
        event.context.get_team_member.return_value = MagicMock(team_member_id="dbmid:leaver", email="leaver@x.com")
        asset = MagicMock()
        asset.is_file.return_value = False
        asset.is_folder.return_value = True
        asset.get_folder.return_value = MagicMock(path=MagicMock(namespace_relative=MagicMock(ns_id="sf-1")))
        event.assets = [asset]
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=DropboxResponse(
            success=True, data=MagicMock(events=[event], cursor="c2", has_more=False),
        ))
        connector.dropbox_cursor_sync_point = MagicMock(update_sync_point=AsyncMock())

        await connector._sync_sharing_changes_with_cursor("key", "c1")

        connector.data_source.sharing_get_folder_metadata.assert_awaited_once_with(
            shared_folder_id="sf-1", team_member_id="dbmid:a",
        )
        connector._shared_folder_members.assert_awaited_once_with("sf-1", "dbmid:a")
        (_, grants), = connector.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert grants == [GRANT]

    @pytest.mark.asyncio
    async def test_a_failed_event_reread_is_logged_as_an_error_with_the_folder(self, connector) -> None:
        connector.data_source.sharing_get_folder_metadata = AsyncMock(return_value=DropboxResponse(success=False, error="no access"))
        connector.logger = MagicMock()

        await connector._refresh_shared_folder("sf-1", "dbmid:leaver")

        assert "sf-1" in connector.logger.error.call_args.args[0]
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()


class TestDeletedSharedFolders:
    """A deleted shared folder stays in sharing/list_folders with no path for anyone (DB-09)."""

    @staticmethod
    def _entry(folder_id: str, path: str | None) -> SimpleNamespace:
        return SimpleNamespace(shared_folder_id=folder_id, name=folder_id, path_lower=path,
                               access_type=SimpleNamespace(_tag="owner"), parent_shared_folder_id=None,
                               access_inheritance=SimpleNamespace(_tag="inherit"), is_team_folder=False)

    def _users(self, connector, listings: list) -> list:
        connector._list_shared_folders = AsyncMock(side_effect=listings)
        connector.data_entities_processor.on_record_group_deleted = AsyncMock()
        return [MagicMock(source_user_id=f"dbmid:{i}") for i in range(len(listings))]

    @pytest.mark.asyncio
    async def test_a_folder_in_no_members_dropbox_is_left_out_and_deleted(self, connector) -> None:
        connector.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=MagicMock())
        users = self._users(connector, [[self._entry("gone", None), self._entry("live", None)],
                                        [self._entry("live", "/live")]])

        plan = await connector._plan_shared_folders(users)

        assert list(plan) == ["live"]
        connector.data_entities_processor.on_record_group_deleted.assert_awaited_once_with(
            external_group_id="gone", connector_id=CONNECTOR_ID, trash_live_records=True,
        )

    @pytest.mark.asyncio
    async def test_nothing_is_deleted_when_a_members_list_could_not_be_read(self, connector) -> None:
        users = self._users(connector, [[self._entry("maybe", None)], None])

        plan = await connector._plan_shared_folders(users)

        assert list(plan) == ["maybe"]
        connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()


class TestListing:
    @pytest.mark.asyncio
    async def test_a_home_listing_leaves_out_shared_folder_content_and_drops_an_old_mount_record(self, connector) -> None:
        mount = FolderMetadata(name="Korean files", id="id:mount", path_lower="/korean files",
                               sharing_info=FolderSharingInfo(read_only=False, shared_folder_id="sf-1"))
        inside = MagicMock(sharing_info=SimpleNamespace(shared_folder_id=None, parent_shared_folder_id="sf-1"))
        own = MagicMock(sharing_info=None)
        connector._process_dropbox_entry = AsyncMock(return_value=None)
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=MagicMock(id="rec-mount"))
        connector.data_entities_processor.on_record_deleted = AsyncMock()

        async for _ in connector._process_dropbox_items_generator(
            [mount, inside, own], "dbmid:a", "a@x.com", "dbmid:a", True, shared_folder_ids=frozenset({"sf-1"}),
        ):
            pass

        assert [call.args[0] for call in connector._process_dropbox_entry.await_args_list] == [own]
        connector.data_entities_processor.on_record_deleted.assert_awaited_once_with(record_id="rec-mount")


class TestMembersWithAccess:
    @pytest.mark.asyncio
    async def test_users_granted_on_the_file_or_above_come_first(self, connector) -> None:
        processor = connector.data_entities_processor
        processor.get_users_with_permission_to_node = AsyncMock(return_value=[MagicMock(email="a@x.com")])
        processor.get_app_user_by_email = AsyncMock(return_value=MagicMock(source_user_id="dbmid:a", is_active=True))

        assert await connector._members_with_access(MagicMock(id="rec-1"), 5) == ["dbmid:a"]
        processor.get_groups_with_access_to_record.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_file_shared_only_through_a_group_is_read_as_its_active_members(self, connector) -> None:
        connector.data_entities_processor.get_groups_with_access_to_record = AsyncMock(
            return_value=[MagicMock(source_user_group_id="g:1")]
        )

        def member(member_id: str, *, active: bool) -> SimpleNamespace:
            status = SimpleNamespace(is_active=lambda: active)
            return SimpleNamespace(profile=SimpleNamespace(team_member_id=member_id, status=status))

        connector._fetch_group_members = AsyncMock(return_value=[member("dbmid:b", active=True), member("dbmid:c", active=False)])

        assert await connector._members_with_access(MagicMock(id="rec-1"), 5) == ["dbmid:b"]

    @pytest.mark.asyncio
    async def test_a_group_whose_members_cannot_be_listed_is_skipped(self, connector) -> None:
        connector.data_entities_processor.get_groups_with_access_to_record = AsyncMock(return_value=[MagicMock()])
        connector._fetch_group_members = AsyncMock(side_effect=RuntimeError("rate limited"))

        assert await connector._members_with_access(MagicMock(id="rec-1"), 5) == []


class TestFileGrants:
    @staticmethod
    def _file() -> FileMetadata:
        modified = datetime(2026, 10, 1, tzinfo=timezone.utc)
        entry = FileMetadata(name="a.txt", id="id:a", client_modified=modified, server_modified=modified,
                             rev="0123456789abcdef", size=10)
        entry.path_lower = entry.path_display = "/a.txt"
        return entry

    def _stub_source(self, connector: DropboxConnector, stored_group: str = "dbmid:a") -> None:
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=MagicMock(id="rec-a", record_name="a.txt", external_revision_id="0123456789abcdef", version=1,
                                   external_record_group_id=stored_group)
        )
        connector.data_source.files_get_temporary_link = AsyncMock(return_value=DropboxResponse(success=False))
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=DropboxResponse(success=True, data=MagicMock(url="https://dropbox.example/a"))
        )
        connector.data_source.files_get_metadata = AsyncMock(return_value=DropboxResponse(success=False))

    @pytest.mark.asyncio
    async def test_a_file_keeps_only_its_own_members_and_inherits_the_rest(self, connector) -> None:
        self._stub_source(connector)
        direct = Permission(email="b@x.com", type=PermissionType.READ, entity_type=EntityType.USER)
        connector._convert_dropbox_permissions_to_permissions = AsyncMock(return_value=[direct])

        update = await connector._process_dropbox_entry(self._file(), "dbmid:a", "a@x.com", "dbmid:a", True)

        assert update.new_permissions == [direct], "no grant is added for the member who synced it"
        assert update.record.inherit_permissions is True
        assert update.record.rewrite_permissions is True

    @pytest.mark.asyncio
    async def test_members_that_could_not_be_read_leave_the_stored_grants(self, connector) -> None:
        self._stub_source(connector)
        connector._convert_dropbox_permissions_to_permissions = AsyncMock(return_value=None)

        update = await connector._process_dropbox_entry(self._file(), "dbmid:a", "a@x.com", "dbmid:a", True)

        assert update.permissions_changed is False
        assert update.record.rewrite_permissions is False

    @pytest.mark.asyncio
    async def test_a_record_left_under_a_members_home_is_moved_to_its_shared_folder(self, connector) -> None:
        """The update path keeps the stored group edge; only on_new_records moves a record."""
        self._stub_source(connector, stored_group="dbmid:a")
        connector._convert_dropbox_permissions_to_permissions = AsyncMock(return_value=[])

        update = await connector._process_dropbox_entry(self._file(), "dbmid:a", "a@x.com", "sf-1", False)

        assert update.is_updated is False
        assert update.record.external_record_group_id == "sf-1"
        assert update.record.rewrite_permissions is True

    @pytest.mark.asyncio
    async def test_a_record_already_in_its_group_takes_the_update_path(self, connector) -> None:
        self._stub_source(connector, stored_group="sf-1")
        connector._convert_dropbox_permissions_to_permissions = AsyncMock(return_value=[])

        update = await connector._process_dropbox_entry(self._file(), "dbmid:a", "a@x.com", "sf-1", False)

        assert update.is_updated is True


def test_the_team_connector_is_registered_as_a_connector() -> None:
    """A helper placed between the builder decorator and the class took the decorator, and the team connector vanished."""
    assert DropboxConnector._connector_metadata["name"] == "Dropbox"
