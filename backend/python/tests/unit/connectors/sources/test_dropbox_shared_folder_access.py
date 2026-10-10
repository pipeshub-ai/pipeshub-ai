"""Dropbox team sync: shared folders that are not mounted, outside collaborators
and deletions.

QA on build 4ef7c1c455 (storage-kb STORAGE-KB-01/-03/-04):

* A shared folder that is not mounted in the member's Dropbox is listed as its
  own namespace. Its top-level files hang off a group named by that namespace,
  and every member of such a file is inherited from the folder, so the file
  carries no grant of its own. The group must carry the folder's members, or
  nobody can read the files, the owner included.
* Collaborators from outside the Dropbox team hold grants but no App gate.
* ``DeletedMetadata`` has no id, so a deletion was dropped.
"""

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dropbox.files import DeletedMetadata, FileMetadata

from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.models.entities import AppUser, RecordGroupType
from app.models.permission import EntityType, PermissionType

OWNER = "harshit@team.test"
EXTERNAL = "outsider@elsewhere.test"
TEAM_FOLDER = "14978535235"
UNMOUNTED = "13163053361"
NESTED = "15099853555"
MOUNTED = "13104043041"
EVERYONE = "g:everyone"


def _folder(ns: str, name: str, *, path_lower=None, team_folder=False, parent=None):
    return SimpleNamespace(
        shared_folder_id=ns, name=name, path_lower=path_lower, is_team_folder=team_folder,
        is_inside_team_folder=parent is not None, parent_shared_folder_id=parent,
        preview_url=f"https://www.dropbox.com/scl/fo/{ns}",
    )


def _user_member(email: str, access: str):
    return SimpleNamespace(access_type=SimpleNamespace(_tag=access), user=SimpleNamespace(email=email))


def _group_member(group_id: str, access: str):
    return SimpleNamespace(access_type=SimpleNamespace(_tag=access), group=SimpleNamespace(group_id=group_id))


def _ok(data):
    return SimpleNamespace(success=True, data=data, error=None)


def _members(users, groups=()):
    return _ok(SimpleNamespace(users=list(users), groups=list(groups), cursor=None, has_more=False))


def _app_user(email: str, member_id: str) -> AppUser:
    return AppUser(
        app_name=Connectors.DROPBOX, connector_id="conn-1", source_user_id=member_id,
        full_name=email, email=email, is_active=True, title="member",
    )


@pytest.fixture()
def processor():
    proc = MagicMock()
    proc.org_id = "org-1"
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_record_deleted = AsyncMock()
    proc.on_external_app_users = AsyncMock()
    proc.reap_external_app_users = AsyncMock(return_value=0)
    proc.on_new_app_users = AsyncMock()
    proc.get_record_by_external_id = AsyncMock(return_value=None)
    proc.get_all_active_users = AsyncMock(return_value=[SimpleNamespace(email=OWNER)])
    proc.get_users_with_permission_to_node = AsyncMock(return_value=[SimpleNamespace(email=OWNER)])
    proc.get_groups_with_permission_to_node = AsyncMock(return_value=[])
    return proc


@pytest.fixture()
def tx_store():
    return AsyncMock()


@pytest.fixture()
def connector(processor, tx_store):
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx_store

    provider.transaction = _transaction
    with patch("app.connectors.sources.dropbox.connector.DropboxApp"):
        conn = DropboxConnector(
            logger=logging.getLogger("test.dropbox.shared"),
            data_entities_processor=processor,
            data_store_provider=provider,
            config_service=AsyncMock(),
            connector_id="conn-1",
            scope="team",
            created_by="creator",
        )
    conn.sync_filters = FilterCollection()
    conn.indexing_filters = FilterCollection()
    conn.data_source = AsyncMock()
    conn._run_sync_with_yield = AsyncMock()
    conn._team_member_ids_by_email = {OWNER: "dbmid:owner"}
    return conn


class TestUnmountedSharedFolderGroups:
    """STORAGE-KB-01: the group a namespace's files hang off carries the folder's members."""

    async def _sync(self, connector, members_by_ns):
        connector.data_source.sharing_list_folders = AsyncMock(return_value=_ok(SimpleNamespace(entries=[
            _folder(TEAM_FOLDER, "Intellysense team folder", team_folder=True),
            _folder(UNMOUNTED, "Test Folder"),
            _folder(NESTED, "Korean files", parent=TEAM_FOLDER),
            _folder(MOUNTED, "Pipeshub team folder", path_lower="/pipeshub team folder"),
        ])))
        connector.data_source.sharing_list_folder_members = AsyncMock(
            side_effect=lambda shared_folder_id, **_: members_by_ns[shared_folder_id]
        )
        await connector._process_users_in_batches([_app_user(OWNER, "dbmid:owner")])
        announced = [
            group for call in connector.data_entities_processor.on_new_record_groups.await_args_list
            for group in call.args[0]
        ]
        return {rg.external_group_id: (rg, perms) for rg, perms in announced}

    async def test_the_owner_of_an_unmounted_folder_gets_its_group(self, connector) -> None:
        groups = await self._sync(connector, {
            UNMOUNTED: _members([_user_member(OWNER, "owner")]),
            NESTED: _members([_user_member(OWNER, "editor")], [_group_member(EVERYONE, "editor")]),
        })

        group, perms = groups[UNMOUNTED]
        assert group.name == "Test Folder"
        assert group.org_id == "org-1"
        assert group.group_type == RecordGroupType.DRIVE
        assert group.parent_external_group_id is None
        assert not group.inherit_permissions
        assert [(p.email, p.type, p.entity_type) for p in perms] == [
            (OWNER, PermissionType.OWNER, EntityType.USER)
        ]

    async def test_a_folder_nested_in_a_team_folder_hangs_under_it_with_its_own_members(self, connector) -> None:
        groups = await self._sync(connector, {
            UNMOUNTED: _members([_user_member(OWNER, "owner")]),
            NESTED: _members([_user_member(OWNER, "editor")], [_group_member(EVERYONE, "editor")]),
        })

        group, perms = groups[NESTED]
        assert group.name == "Korean files"
        assert group.parent_external_group_id == TEAM_FOLDER
        assert not group.inherit_permissions
        assert {(p.external_id or p.email, p.entity_type) for p in perms} == {
            (OWNER, EntityType.USER), (EVERYONE, EntityType.GROUP)
        }

    async def test_team_folders_and_mounted_folders_are_left_to_their_own_sync(self, connector) -> None:
        groups = await self._sync(connector, {
            UNMOUNTED: _members([_user_member(OWNER, "owner")]),
            NESTED: _members([_user_member(OWNER, "editor")]),
        })

        assert set(groups) == {UNMOUNTED, NESTED}

    async def test_members_that_cannot_be_read_keep_the_stored_grants(self, connector) -> None:
        groups = await self._sync(connector, {
            UNMOUNTED: SimpleNamespace(success=False, data=None, error="access_error"),
            NESTED: _members([_user_member(OWNER, "editor")]),
        })

        group, perms = groups[UNMOUNTED]
        assert group.name == "Test Folder"
        assert perms is None


class TestExternalCollaboratorsAreGated:
    """STORAGE-KB-03: a grant to someone outside the Dropbox team gates them into the App."""

    async def test_an_outside_editor_of_a_shared_folder_gets_app_membership(self, connector, processor) -> None:
        connector._team_member_emails = {OWNER}
        connector.data_source.sharing_list_folders = AsyncMock(
            return_value=_ok(SimpleNamespace(entries=[_folder(UNMOUNTED, "Test Folder")]))
        )
        connector.data_source.sharing_list_folder_members = AsyncMock(return_value=_members([
            _user_member(OWNER, "owner"), _user_member(EXTERNAL.upper(), "editor"),
        ]))

        await connector._process_users_in_batches([_app_user(OWNER, "dbmid:owner")])
        await connector._flush_external_app_users()

        processor.on_external_app_users.assert_awaited_once_with([EXTERNAL], "conn-1")

    async def test_no_team_member_list_flags_nobody(self, connector, processor) -> None:
        connector._team_member_emails = set()
        connector._track_external_collaborators([
            SimpleNamespace(entity_type=EntityType.USER, email=EXTERNAL),
        ])

        await connector._flush_external_app_users()

        processor.on_external_app_users.assert_not_awaited()

    @patch("app.connectors.sources.dropbox.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_a_full_sync_flushes_then_reaps_after_the_drives(self, load_filters, connector, processor) -> None:
        load_filters.return_value = (FilterCollection(), FilterCollection())
        profile = SimpleNamespace(
            team_member_id="dbmid:owner", name=SimpleNamespace(display_name="Owner"), email=OWNER,
            status=SimpleNamespace(_tag="active"),
        )
        connector.data_source.team_members_list = AsyncMock(
            return_value=SimpleNamespace(success=True, data=SimpleNamespace(members=[
                SimpleNamespace(profile=profile, role=SimpleNamespace(_tag="team_admin")),
            ], has_more=False))
        )
        connector.dropbox_cursor_sync_point = AsyncMock()
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
        connector._initialize_event_cursor = AsyncMock()
        connector._sync_user_groups = AsyncMock()
        connector.sync_record_groups = AsyncMock()
        connector.sync_personal_record_groups = AsyncMock()
        order = []

        async def drives(users):
            assert connector._team_member_emails == {OWNER}
            connector._track_external_collaborators([
                SimpleNamespace(entity_type=EntityType.USER, email=EXTERNAL),
                SimpleNamespace(entity_type=EntityType.USER, email=OWNER),
                SimpleNamespace(entity_type=EntityType.GROUP, email=None),
            ])
            order.append("drives")

        connector._process_users_in_batches = AsyncMock(side_effect=drives)
        processor.on_external_app_users.side_effect = lambda *a: order.append("flush")
        processor.reap_external_app_users.side_effect = lambda *a: order.append("reap") or 0

        await connector.run_sync()

        processor.on_external_app_users.assert_awaited_once_with([EXTERNAL], "conn-1")
        assert order == ["drives", "flush", "reap"]


def _deleted(path: str) -> DeletedMetadata:
    entry = DeletedMetadata(name=path.rsplit("/", 1)[-1])
    entry.path_lower = path
    return entry


def _stored(record_id: str, external_id: str):
    return SimpleNamespace(id=record_id, external_record_id=external_id, parent_external_record_id=None)


def _not_found():
    return SimpleNamespace(success=False, data=None, error="ApiError('path/not_found/')")


class TestDeletionsAreApplied:
    """STORAGE-KB-04: a deleted path removes the records stored at and below it."""

    async def test_a_deleted_file_is_removed(self, connector, processor, tx_store) -> None:
        tx_store.get_file_records_under_path = AsyncMock(return_value=[_stored("rec-1", "id:f1")])
        connector.data_source.files_get_metadata = AsyncMock(return_value=_not_found())

        result = await connector._process_dropbox_entry(
            _deleted("/test folder/a.txt"), user_id="dbmid:owner", user_email=OWNER,
            record_group_id=UNMOUNTED, is_person_folder=False,
        )

        assert result is None
        tx_store.get_file_records_under_path.assert_awaited_once_with("conn-1", UNMOUNTED, "/test folder/a.txt")
        processor.on_record_deleted.assert_awaited_once_with(record_id="rec-1")

    async def test_a_deleted_folder_takes_what_is_below_it_deepest_first(self, connector, processor, tx_store) -> None:
        tx_store.get_file_records_under_path = AsyncMock(return_value=[
            _stored("rec-deep", "id:deep"), _stored("rec-child", "id:child"), _stored("rec-dir", "id:dir"),
        ])
        connector.data_source.files_get_metadata = AsyncMock(return_value=_not_found())

        await connector._process_dropbox_entry(
            _deleted("/docs"), user_id="dbmid:owner", user_email=OWNER,
            record_group_id="dbmid:owner", is_person_folder=True,
        )

        assert [c.kwargs["record_id"] for c in processor.on_record_deleted.await_args_list] == [
            "rec-deep", "rec-child", "rec-dir",
        ]

    async def test_a_moved_item_is_not_deleted(self, connector, processor, tx_store) -> None:
        """A move lists the old path as deleted; the item still answers to its id."""
        tx_store.get_file_records_under_path = AsyncMock(return_value=[_stored("rec-1", "id:f1")])
        moved = FileMetadata(name="a.txt", id="id:f1", path_lower="/elsewhere/a.txt")
        connector.data_source.files_get_metadata = AsyncMock(return_value=_ok(moved))

        await connector._process_dropbox_entry(
            _deleted("/test folder/a.txt"), user_id="dbmid:owner", user_email=OWNER,
            record_group_id=UNMOUNTED, is_person_folder=False,
        )

        connector.data_source.files_get_metadata.assert_awaited_once_with(
            "id:f1", team_member_id="dbmid:owner", team_folder_id=UNMOUNTED,
        )
        processor.on_record_deleted.assert_not_awaited()

    async def test_an_unreadable_source_deletes_nothing(self, connector, processor, tx_store) -> None:
        tx_store.get_file_records_under_path = AsyncMock(return_value=[_stored("rec-1", "id:f1")])
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=SimpleNamespace(success=False, data=None, error="503 Service Unavailable")
        )

        await connector._process_dropbox_entry(
            _deleted("/a.txt"), user_id="dbmid:owner", user_email=OWNER,
            record_group_id="dbmid:owner", is_person_folder=True,
        )

        processor.on_record_deleted.assert_not_awaited()

    async def test_the_sync_loop_hands_deletions_through(self, connector, processor, tx_store) -> None:
        tx_store.get_file_records_under_path = AsyncMock(return_value=[_stored("rec-1", "id:f1")])
        connector.data_source.files_get_metadata = AsyncMock(return_value=_not_found())
        del connector._run_sync_with_yield
        connector.dropbox_cursor_sync_point = AsyncMock()
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={"cursor": "c1"})
        connector.data_source.sharing_list_folders = AsyncMock(return_value=_ok(SimpleNamespace(entries=[])))
        connector.data_source.files_list_folder_continue = AsyncMock(return_value=_ok(SimpleNamespace(
            entries=[_deleted("/a.txt")], cursor="c2", has_more=False,
        )))

        await connector._run_sync_with_yield("dbmid:owner", OWNER)

        processor.on_record_deleted.assert_awaited_once_with(record_id="rec-1")


MEMBER = "member@team.test"
GROUP_MEMBER = "grouped@team.test"


def _held(record_id: str, external_id: str, parent: str | None = None):
    return SimpleNamespace(id=record_id, external_record_id=external_id, parent_external_record_id=parent)


def _group_profile(email: str, member_id: str):
    return SimpleNamespace(profile=SimpleNamespace(team_member_id=member_id, email=email))


class TestAMemberWhoLeavesASharedFolder:
    """R1-05: a member's listing is that member's view. Leaving, or being removed from,
    a shared folder lists it as deleted for them, and they no longer find its files,
    but the other members still have them."""

    @pytest.fixture()
    def shared(self, connector, processor, tx_store):
        connector._team_member_ids_by_email = {OWNER: "dbmid:owner", MEMBER: "dbmid:member"}
        tx_store.get_file_records_under_path = AsyncMock(return_value=[
            _held("rec-file", "id:file", parent="id:dir"), _held("rec-dir", "id:dir"),
        ])
        processor.get_users_with_permission_to_node = AsyncMock(return_value=[
            SimpleNamespace(email=OWNER), SimpleNamespace(email=MEMBER), SimpleNamespace(email=EXTERNAL),
        ])
        processor.get_groups_with_permission_to_node = AsyncMock(return_value=[])
        connector._handle_record_updates = AsyncMock()
        return connector

    def _answers(self, connector, by_member):
        async def get_metadata(path, team_member_id=None, team_folder_id=None, **_):
            return by_member[team_member_id](path)
        connector.data_source.files_get_metadata = AsyncMock(side_effect=get_metadata)

    async def test_the_files_stay_for_the_members_who_still_have_them(self, shared, processor) -> None:
        self._answers(shared, {
            "dbmid:owner": lambda path: _not_found(),
            "dbmid:member": lambda path: _ok(FileMetadata(
                name=path, id=path, path_lower=f"/work/shared/{path}",
            )),
        })
        resynced = []

        async def as_member(entry, user_id, user_email, record_group_id, is_person_folder, **_):
            resynced.append((entry.id, user_id, record_group_id, is_person_folder))
            return SimpleNamespace(is_updated=True, metadata_changed=False)

        shared._process_dropbox_entry = AsyncMock(side_effect=as_member)

        await shared._delete_records_at_path("/shared", "dbmid:owner", "dbmid:owner", True)

        processor.on_record_deleted.assert_not_awaited()
        assert resynced == [
            ("id:dir", "dbmid:member", "dbmid:member", True),
            ("id:file", "dbmid:member", "dbmid:member", True),
        ]
        moved = [c.args[0] for c in shared._handle_record_updates.await_args_list]
        assert [(u.is_updated, u.metadata_changed) for u in moved] == [(True, True), (True, True)]

    async def test_a_file_the_first_holder_lacks_is_synced_as_one_who_has_it(self, shared, processor) -> None:
        third = "third@team.test"
        shared._team_member_ids_by_email[third] = "dbmid:third"
        processor.get_users_with_permission_to_node = AsyncMock(side_effect=lambda node_id, *_, **__: [
            SimpleNamespace(email=OWNER), SimpleNamespace(email=MEMBER),
        ] + ([SimpleNamespace(email=third)] if node_id == "rec-file" else []))

        def sees(*ids):
            return lambda path: _ok(FileMetadata(name=path, id=path, path_lower=f"/{path}")) if path in ids else _not_found()

        self._answers(shared, {
            "dbmid:owner": lambda path: _not_found(), "dbmid:member": sees("id:dir"), "dbmid:third": sees("id:file"),
        })
        shared._process_dropbox_entry = AsyncMock(return_value=SimpleNamespace(is_updated=True, metadata_changed=False))

        await shared._delete_records_at_path("/shared", "dbmid:owner", "dbmid:owner", True)

        processor.on_record_deleted.assert_not_awaited()
        assert [(c.args[0].id, c.args[1]) for c in shared._process_dropbox_entry.await_args_list] == [
            ("id:dir", "dbmid:member"), ("id:file", "dbmid:third"),
        ]

    async def test_a_file_no_holder_finds_is_deleted_and_its_folder_kept(self, shared, processor) -> None:
        self._answers(shared, {
            "dbmid:owner": lambda path: _not_found(),
            "dbmid:member": lambda path: _ok(FileMetadata(name=path, id=path, path_lower="/shared"))
            if path == "id:dir" else _not_found(),
        })
        shared._process_dropbox_entry = AsyncMock(return_value=SimpleNamespace(is_updated=True, metadata_changed=False))

        await shared._delete_records_at_path("/shared", "dbmid:owner", "dbmid:owner", True)

        assert [c.kwargs["record_id"] for c in processor.on_record_deleted.await_args_list] == ["rec-file"]

    async def test_files_no_member_finds_are_deleted(self, shared, processor) -> None:
        self._answers(shared, {"dbmid:owner": lambda path: _not_found(), "dbmid:member": lambda path: _not_found()})

        await shared._delete_records_at_path("/shared", "dbmid:owner", "dbmid:owner", True)

        assert [c.kwargs["record_id"] for c in processor.on_record_deleted.await_args_list] == ["rec-file", "rec-dir"]

    async def test_a_member_who_cannot_be_asked_keeps_the_files(self, shared, processor) -> None:
        self._answers(shared, {
            "dbmid:owner": lambda path: _not_found(),
            "dbmid:member": lambda path: SimpleNamespace(success=False, data=None, error="too_many_requests"),
        })

        await shared._delete_records_at_path("/shared", "dbmid:owner", "dbmid:owner", True)

        processor.on_record_deleted.assert_not_awaited()

    async def test_a_member_of_a_group_it_is_shared_with_keeps_the_files(self, shared, processor) -> None:
        processor.get_users_with_permission_to_node = AsyncMock(return_value=[SimpleNamespace(email=OWNER)])
        processor.get_groups_with_permission_to_node = AsyncMock(
            side_effect=lambda node_id, *_, **__: [SimpleNamespace(source_user_group_id=EVERYONE)]
            if node_id == "rec-dir" else []
        )
        processor.get_record_by_external_id = AsyncMock(return_value=_held("rec-dir", "id:dir"))
        shared._fetch_group_members = AsyncMock(return_value=[_group_profile(GROUP_MEMBER, "dbmid:grouped")])
        self._answers(shared, {
            "dbmid:owner": lambda path: _not_found(),
            "dbmid:grouped": lambda path: _ok(FileMetadata(name=path, id=path, path_lower=f"/{path}")),
        })
        shared._process_dropbox_entry = AsyncMock(return_value=SimpleNamespace(is_updated=True, metadata_changed=False))

        await shared._delete_records_at_path("/shared", "dbmid:owner", "dbmid:owner", True)

        processor.on_record_deleted.assert_not_awaited()
        shared._fetch_group_members.assert_awaited_once_with(EVERYONE, EVERYONE, raise_on_partial=True)

    async def test_a_file_only_its_owner_had_is_deleted_without_asking_anyone(self, shared, processor) -> None:
        processor.get_users_with_permission_to_node = AsyncMock(return_value=[SimpleNamespace(email=OWNER)])
        self._answers(shared, {"dbmid:owner": lambda path: _not_found()})

        await shared._delete_records_at_path("/shared", "dbmid:owner", "dbmid:owner", True)

        assert processor.on_record_deleted.await_count == 2
        assert {c.kwargs["team_member_id"] for c in shared.data_source.files_get_metadata.await_args_list} == {
            "dbmid:owner"
        }


def _failed(error="too_many_requests"):
    return SimpleNamespace(success=False, data=None, error=error)


class TestAccessKeptOnAFailedReadSurvivesTheSweep:
    """R1-06: a member list Dropbox could not read keeps the stored one, so a full
    sync must not sweep it afterwards."""

    async def test_a_team_folder_whose_members_cannot_be_read_is_not_swept(self, connector, processor) -> None:
        from app.connectors.core.sync.sync_runner import run_sync_task

        admin = _app_user(OWNER, "dbmid:owner")
        admin.title = "team_admin"
        connector.data_source.team_team_folder_list = AsyncMock(return_value=_ok(SimpleNamespace(
            team_folders=[SimpleNamespace(team_folder_id=TEAM_FOLDER, name="Intellysense", status=SimpleNamespace(_tag="active"))],
            cursor=None, has_more=False,
        )))
        connector.data_source.sharing_list_folder_members = AsyncMock(return_value=_failed())

        async def full_sync():
            await connector.sync_record_groups([admin])

        connector.run_sync = full_sync
        graph = AsyncMock()
        graph.sweep_connector_sync_edges = AsyncMock(return_value=(1, True))

        await run_sync_task(connector, "conn-1", graph, logging.getLogger("t"), sweep_generation=1)

        processor.on_new_record_groups.assert_not_awaited()
        graph.sweep_connector_sync_edges.assert_not_awaited()

    async def test_an_unmounted_folder_whose_members_cannot_be_read_keeps_access(self, connector) -> None:
        connector.data_source.sharing_list_folders = AsyncMock(
            return_value=_ok(SimpleNamespace(entries=[_folder(UNMOUNTED, "Test Folder")]))
        )
        connector.data_source.sharing_list_folder_members = AsyncMock(return_value=_failed())

        await connector._process_users_in_batches([_app_user(OWNER, "dbmid:owner")])

        assert UNMOUNTED in connector.stored_access_kept

    async def test_a_member_page_that_cannot_be_read_keeps_the_stored_members(self, connector) -> None:
        first = _ok(SimpleNamespace(users=[_user_member(OWNER, "owner")], groups=[], cursor="c1", has_more=True))
        connector.data_source.sharing_list_folder_members = AsyncMock(return_value=first)
        connector.data_source.sharing_list_folder_members_continue = AsyncMock(return_value=_failed())

        permissions = await connector._fetch_shared_folder_permissions(UNMOUNTED, "Test Folder", "dbmid:owner")

        assert permissions is None

    async def test_a_group_whose_members_cannot_be_read_keeps_access(self, connector, processor) -> None:
        processor.on_new_user_groups = AsyncMock()
        connector.data_source.team_groups_list = AsyncMock(return_value=_ok(SimpleNamespace(
            groups=[SimpleNamespace(group_id=EVERYONE, group_name="Everyone")], cursor=None, has_more=False,
        )))
        connector.data_source.team_groups_members_list = AsyncMock(return_value=_failed())

        await connector._sync_user_groups()

        assert EVERYONE in connector.stored_access_kept

    async def test_a_member_whose_dropbox_fails_to_sync_keeps_access(self, connector) -> None:
        connector.data_source.sharing_list_folders = AsyncMock(return_value=_ok(SimpleNamespace(entries=[])))
        connector._run_sync_with_yield = AsyncMock(side_effect=RuntimeError("rate limited"))

        await connector._process_users_in_batches([_app_user(OWNER, "dbmid:owner")])

        assert OWNER in connector.stored_access_kept

    async def test_a_listing_that_fails_keeps_access(self, connector) -> None:
        del connector._run_sync_with_yield
        connector.dropbox_cursor_sync_point = AsyncMock()
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
        connector.data_source.sharing_list_folders = AsyncMock(return_value=_ok(SimpleNamespace(entries=[])))
        connector.data_source.files_list_folder = AsyncMock(return_value=_failed())

        await connector._run_sync_with_yield("dbmid:owner", OWNER)

        assert "personal folder" in connector.stored_access_kept


class TestAccessLevels:
    """R1-15: only owner, editor and the two viewer levels open an item. Traverse
    (a restricted folder's name only), no_access and a level this code does not
    know grant nothing."""

    async def test_a_folder_member_without_read_access_gets_no_grant(self, connector) -> None:
        connector.data_source.sharing_list_folder_members = AsyncMock(return_value=_members(
            [
                _user_member(OWNER, "owner"),
                _user_member("viewer@team.test", "viewer_no_comment"),
                _user_member("traverse@team.test", "traverse"),
                _user_member("none@team.test", "no_access"),
                _user_member("future@team.test", "other"),
            ],
            [_group_member(EVERYONE, "traverse")],
        ))

        permissions = await connector._fetch_shared_folder_permissions(TEAM_FOLDER, "Team", "dbmid:owner")

        assert [(p.email or p.external_id, p.type) for p in permissions] == [
            (OWNER, PermissionType.OWNER), ("viewer@team.test", PermissionType.READ),
        ]

    async def test_a_file_member_without_read_access_gets_no_grant(self, connector) -> None:
        def member(email, access):
            return SimpleNamespace(
                access_type=SimpleNamespace(_tag=access), is_inherited=False,
                user=SimpleNamespace(email=email, account_id=f"dbid:{email}"),
            )

        connector.data_source.sharing_list_file_members = AsyncMock(return_value=_ok(SimpleNamespace(
            users=[member(OWNER, "editor"), member("traverse@team.test", "traverse")],
            groups=[SimpleNamespace(access_type=SimpleNamespace(_tag="no_access"), is_inherited=False,
                                    group=SimpleNamespace(group_id=EVERYONE))],
            invitees=[SimpleNamespace(access_type=SimpleNamespace(_tag="other"), is_inherited=False,
                                      invitee=SimpleNamespace(email="invited@elsewhere.test"))],
        )))

        permissions = await connector._convert_dropbox_permissions_to_permissions(
            "id:f1", is_file=True, team_member_id="dbmid:owner",
        )

        assert [(p.email, p.type) for p in permissions] == [(OWNER, PermissionType.WRITE)]


def _profile_member(email: str, member_id: str):
    profile = SimpleNamespace(
        team_member_id=member_id, name=SimpleNamespace(display_name=email), email=email,
        status=SimpleNamespace(_tag="active"),
    )
    return SimpleNamespace(profile=profile, role=SimpleNamespace(_tag="member_only"))


class TestTheWholeTeamIsListed:
    """R1-17: team/members/list pages at 1000 members; a member past the first page
    was missing from the team and read as an outside collaborator."""

    @pytest.fixture()
    def full_sync(self, connector):
        connector.dropbox_cursor_sync_point = AsyncMock()
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
        connector._initialize_event_cursor = AsyncMock()
        connector._sync_user_groups = AsyncMock()
        connector.sync_record_groups = AsyncMock()
        connector.sync_personal_record_groups = AsyncMock()
        connector._process_users_in_batches = AsyncMock()
        with patch(
            "app.connectors.sources.dropbox.connector.load_connector_filters", new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            yield connector

    async def test_a_member_on_a_later_page_is_a_team_member(self, full_sync, processor) -> None:
        full_sync.data_source.team_members_list = AsyncMock(return_value=_ok(SimpleNamespace(
            members=[_profile_member(OWNER, "dbmid:owner")], cursor="c1", has_more=True,
        )))
        full_sync.data_source.team_members_list_continue = AsyncMock(return_value=_ok(SimpleNamespace(
            members=[_profile_member(MEMBER, "dbmid:member")], cursor="c2", has_more=False,
        )))

        await full_sync.run_sync()

        full_sync.data_source.team_members_list_continue.assert_awaited_once_with("c1")
        assert full_sync._team_member_emails == {OWNER, MEMBER}
        assert [u.email for u in processor.on_new_app_users.await_args.args[0]] == [OWNER, MEMBER]

    async def test_a_later_page_that_cannot_be_read_fails_the_sync(self, full_sync, processor) -> None:
        full_sync.data_source.team_members_list = AsyncMock(return_value=_ok(SimpleNamespace(
            members=[_profile_member(OWNER, "dbmid:owner")], cursor="c1", has_more=True,
        )))
        full_sync.data_source.team_members_list_continue = AsyncMock(return_value=_failed())

        with pytest.raises(RuntimeError, match="team members"):
            await full_sync.run_sync()

        processor.on_new_app_users.assert_not_awaited()
