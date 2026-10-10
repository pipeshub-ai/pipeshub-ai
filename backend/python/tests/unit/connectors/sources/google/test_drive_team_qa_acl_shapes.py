"""Drive Workspace access, driven by the ACL shapes the 2026-10-10 QA read from the live tenant.

Every metadata and ``permissions.list`` payload below is copied from
``neo4j/qa/google/drive/data/src.file.jsonl`` and ``src.perm.jsonl`` (Drive
Workspace 05388601). Each item goes through ``_process_drive_item`` as the sync
would run it, and ``_readers`` then walks the stored grants and inherit flags
the way the graph's per-hop rule does.
"""

import logging
from typing import Dict, List, Optional, Set
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.filters import FilterCollection
from app.models.permission import EntityType

VISHWJEET = "vishwjeet.pawar@pipeshub.app"
TEST = "test@pipeshub.app"
ABHISHEK = "abhishek@pipeshub.app"
MY_DRIVE = "0ACDVv1Ps_qNIUk9PVA"

ASANA = "1i9Q7O3FEUVBb6PYUf9egFqceo8FfkEIl"
HIDDEN = "1wmaImLeJ5J_aMz8v0OpYsob1lgBsInn7"
HIDDEN_S1 = "1ZChhHCf57w6PHkoMQ_da18IH5AzHLZEZ"
PDF_UNDER_HIDDEN = "1orW80uEpMC6Awot0KsjUfziBPqg-z65s"
BOX_TXT = "1sZHJqjFzC6y7NbphLqLJd-tQ1_Ttme-Q"

_VISHWJEET_OWNER = {
    "id": "07431531268217860255", "type": "user",
    "permissionDetails": [
        {"permissionType": "file", "role": "writer", "inherited": True},
        {"permissionType": "file", "role": "owner", "inherited": False},
    ],
    "emailAddress": VISHWJEET, "role": "owner", "deleted": False, "pendingOwner": False,
}


def _folder(file_id: str, name: str, parent: str, *, shared: bool, limited: bool) -> dict:
    return {
        "mimeType": "application/vnd.google-apps.folder", "parents": [parent], "shared": shared,
        "owners": [{"emailAddress": VISHWJEET}], "id": file_id, "name": name, "trashed": False,
        "createdTime": "2026-02-23T12:10:30.134Z", "modifiedTime": "2026-02-23T12:13:23.341Z",
        "inheritedPermissionsDisabled": limited,
    }


def _file(file_id: str, name: str, mime: str, parent: str) -> dict:
    return {
        "mimeType": mime, "parents": [parent], "shared": False,
        "owners": [{"emailAddress": VISHWJEET}], "id": file_id, "name": name, "trashed": False,
        "createdTime": "2026-07-27T10:52:16.523Z", "modifiedTime": "2026-07-22T07:47:15.000Z",
        "inheritedPermissionsDisabled": False,
    }


LIMITED_FOLDER_TREE: Dict[str, dict] = {
    ASANA: _folder(ASANA, "Asana", MY_DRIVE, shared=True, limited=False),
    HIDDEN: _folder(HIDDEN, "hidden", ASANA, shared=False, limited=True),
    HIDDEN_S1: _folder(HIDDEN_S1, "hidden s1", HIDDEN, shared=False, limited=False),
    PDF_UNDER_HIDDEN: _file(PDF_UNDER_HIDDEN, "100mb.pdf", "application/pdf", HIDDEN),
    BOX_TXT: _file(BOX_TXT, "Box User group sync.txt", "text/plain", HIDDEN_S1),
}

LIMITED_FOLDER_ACLS: Dict[str, List[dict]] = {
    ASANA: [
        {"id": "03664130474517121712", "type": "user",
         "permissionDetails": [{"permissionType": "file", "role": "reader", "inherited": False}],
         "emailAddress": ABHISHEK, "role": "reader", "deleted": False, "pendingOwner": False},
        _VISHWJEET_OWNER,
        {"id": "12067073765715526032", "type": "user",
         "permissionDetails": [{"permissionType": "file", "role": "writer", "inherited": False}],
         "emailAddress": TEST, "role": "writer", "deleted": False, "pendingOwner": False},
    ],
    HIDDEN: [
        {"id": "03664130474517121712", "type": "user",
         "permissionDetails": [{"permissionType": "file", "role": "reader", "inherited": True}],
         "emailAddress": ABHISHEK, "role": "reader", "deleted": False, "view": "metadata", "pendingOwner": False},
        _VISHWJEET_OWNER,
        {"id": "12067073765715526032", "type": "user",
         "permissionDetails": [{"permissionType": "file", "role": "writer", "inherited": True}],
         "emailAddress": TEST, "role": "reader", "deleted": False, "view": "metadata", "pendingOwner": False},
    ],
    HIDDEN_S1: [_VISHWJEET_OWNER],
    PDF_UNDER_HIDDEN: [_VISHWJEET_OWNER],
    BOX_TXT: [_VISHWJEET_OWNER],
}


def _make_connector():
    with patch(
        "app.connectors.sources.google.drive.team.connector.GoogleDriveTeamApp"
    ), patch(
        "app.connectors.sources.google.drive.team.connector.SyncPoint"
    ):
        from app.connectors.sources.google.drive.team.connector import (
            GoogleDriveTeamConnector,
        )

        dep = MagicMock()
        dep.org_id = "org-qa"
        dep.get_record_by_external_id = AsyncMock(return_value=None)
        dep.add_permission_to_record = AsyncMock()
        dep.inheritance_when_unreadable = AsyncMock(return_value=False)
        conn = GoogleDriveTeamConnector(
            logger=logging.getLogger("drive-qa-shapes"),
            data_entities_processor=dep,
            data_store_provider=MagicMock(),
            config_service=AsyncMock(),
            connector_id="05388601-5ac1-4d04-a2b6-7e5c08579cc4",
            scope="team",
            created_by="qa",
        )
    conn.connector_name = Connectors.GOOGLE_DRIVE_WORKSPACE
    conn.sync_filters = FilterCollection()
    conn.indexing_filters = FilterCollection()
    conn.synced_user_emails = {
        ABHISHEK, "noreply@pipeshubapp.com", "rishabh@pipeshub.app",
        "shekhar@pipeshub.app", TEST, VISHWJEET,
    }
    return conn


def _data_source(acls: Dict[str, List[dict]]) -> MagicMock:
    source = MagicMock()

    async def permissions_list(**params):
        return {"permissions": acls[params["fileId"]]}

    source.permissions_list = AsyncMock(side_effect=permissions_list)
    return source


async def _sync(conn, tree: Dict[str, dict], acls: Dict[str, List[dict]], *, as_user: str, drive_id: str,
                is_shared_drive: bool = False) -> Dict[str, object]:
    source = _data_source(acls)
    updates = {}
    for file_id, metadata in tree.items():
        update = await conn._process_drive_item(
            dict(metadata), "perm-id", as_user, drive_id,
            is_shared_drive=is_shared_drive, drive_data_source=source,
        )
        assert update is not None, file_id
        updates[file_id] = update
    return updates


def _stored_principals(update) -> Set[str]:
    return {p.email if p.entity_type == EntityType.USER else p.external_id for p in update.new_permissions}


def _readers(updates: Dict[str, object], file_id: str, group_readers: Set[str]) -> Set[str]:
    """Own grants, plus the parent's readers when the item inherits; the group above the top."""
    update = updates[file_id]
    readers = _stored_principals(update)
    if not update.record.inherit_permissions:
        return readers
    parent: Optional[str] = update.record.parent_external_record_id
    if parent is None or parent not in updates:
        return readers | group_readers
    return readers | _readers(updates, parent, group_readers)


class TestLimitedFolderOnRealShapes:
    """N4GOOGLE-01 / GDRIVE-01: `hidden` shows test@ and abhishek@ its name only."""

    @pytest.mark.asyncio
    async def test_hidden_stores_only_its_owner_and_does_not_inherit(self) -> None:
        conn = _make_connector()
        updates = await _sync(conn, LIMITED_FOLDER_TREE, LIMITED_FOLDER_ACLS, as_user=VISHWJEET, drive_id=MY_DRIVE)

        hidden = updates[HIDDEN]
        assert hidden.permissions_changed is True
        assert _stored_principals(hidden) == {VISHWJEET}
        assert hidden.record.inherit_permissions is False

    @pytest.mark.asyncio
    async def test_children_inherit_from_hidden_and_reach_only_the_owner(self) -> None:
        conn = _make_connector()
        updates = await _sync(conn, LIMITED_FOLDER_TREE, LIMITED_FOLDER_ACLS, as_user=VISHWJEET, drive_id=MY_DRIVE)
        my_drive_group = {VISHWJEET}

        assert updates[HIDDEN_S1].record.parent_external_record_id == HIDDEN
        assert updates[PDF_UNDER_HIDDEN].record.parent_external_record_id == HIDDEN
        assert updates[BOX_TXT].record.parent_external_record_id == HIDDEN_S1
        for child in (HIDDEN_S1, PDF_UNDER_HIDDEN, BOX_TXT):
            assert updates[child].record.inherit_permissions is True
            assert _readers(updates, child, my_drive_group) == {VISHWJEET}, child

    @pytest.mark.asyncio
    async def test_test_user_keeps_asana_and_loses_the_limited_subtree(self) -> None:
        conn = _make_connector()
        updates = await _sync(conn, LIMITED_FOLDER_TREE, LIMITED_FOLDER_ACLS, as_user=VISHWJEET, drive_id=MY_DRIVE)
        my_drive_group = {VISHWJEET}

        readable = {fid for fid in updates if TEST in _readers(updates, fid, my_drive_group)}
        assert readable == {ASANA}

    @pytest.mark.asyncio
    async def test_metadata_view_is_dropped_even_without_permission_details(self) -> None:
        """Google documents ``view: metadata`` as name-only; it must not depend on permissionDetails."""
        stripped = {
            HIDDEN: [{k: v for k, v in perm.items() if k != "permissionDetails"} for perm in LIMITED_FOLDER_ACLS[HIDDEN]]
        }
        conn = _make_connector()
        updates = await _sync(conn, {HIDDEN: LIMITED_FOLDER_TREE[HIDDEN]}, stripped, as_user=VISHWJEET, drive_id=MY_DRIVE)

        assert _stored_principals(updates[HIDDEN]) == {VISHWJEET}

    @pytest.mark.asyncio
    async def test_permissions_list_asks_for_the_view(self) -> None:
        conn = _make_connector()
        source = _data_source(LIMITED_FOLDER_ACLS)
        await conn._fetch_permissions(HIDDEN, user_email=VISHWJEET, drive_data_source=source,
                                      inherited_permissions_disabled=True)
        assert "view" in source.permissions_list.call_args.kwargs["fields"]


OTHER_TENANT_DRIVE = "0APG2DS8x81fJUk9PVA"
ADMIN_LISTED_DRIVE = "0AOeqnxqksNRIUk9PVA"
ORGANIZER = "test@pipeshub.info"
EXT_PDF = "1thC9TTbqrAeZK5A1K3hwqKAKbls-w__X"
EXT_HTML = "1H393R2k5jbSC9AZW7btiaQtt4tussSSW"
EXT_FOLDER = "13t38bfuX9Gf91bvrxhYjrWZnej_JJrfN"
EXT_CHESS = "15HZbFxG_cBqnOVUDes8zfZNt_YSHVFMl"


def _member(email: str, perm_id: str, role: str) -> dict:
    return {
        "id": perm_id, "type": "user",
        "permissionDetails": [{"permissionType": "member", "inheritedFrom": OTHER_TENANT_DRIVE,
                               "role": role, "inherited": True}],
        "emailAddress": email, "role": role, "deleted": False,
    }


_EXT_ORGANIZER = _member(ORGANIZER, "01448361410974402662", "organizer")
_EXT_VISHWJEET = _member(VISHWJEET, "07431531268217860255", "reader")


def _ext_item(file_id: str, name: str, mime: str, parent: str, *, limited: bool = False) -> dict:
    return {
        "driveId": OTHER_TENANT_DRIVE, "mimeType": mime, "parents": [parent], "id": file_id, "name": name,
        "trashed": False, "createdTime": "2026-08-22T09:51:52.020Z", "modifiedTime": "2026-06-17T13:39:11.000Z",
        "inheritedPermissionsDisabled": limited,
    }


OTHER_TENANT_TREE: Dict[str, dict] = {
    EXT_PDF: _ext_item(EXT_PDF, "100mb.pdf", "application/pdf", OTHER_TENANT_DRIVE),
    EXT_HTML: _ext_item(EXT_HTML, "document.html", "text/html", OTHER_TENANT_DRIVE),
    EXT_FOLDER: _ext_item(EXT_FOLDER, "Untitled folder", "application/vnd.google-apps.folder", OTHER_TENANT_DRIVE),
    EXT_CHESS: _ext_item(EXT_CHESS, "chessHistory.doc", "application/msword", EXT_FOLDER),
}

OTHER_TENANT_ACLS: Dict[str, List[dict]] = {
    EXT_PDF: [_EXT_ORGANIZER, _EXT_VISHWJEET],
    EXT_HTML: [_EXT_ORGANIZER, _EXT_VISHWJEET],
    EXT_FOLDER: [
        _EXT_ORGANIZER, _EXT_VISHWJEET,
        {"id": "12067073765715526032", "type": "user",
         "permissionDetails": [{"permissionType": "file", "role": "reader", "inherited": False}],
         "emailAddress": TEST, "role": "reader", "deleted": False},
    ],
    EXT_CHESS: [
        _EXT_ORGANIZER, _EXT_VISHWJEET,
        {"id": "12067073765715526032", "type": "user",
         "permissionDetails": [{"permissionType": "file", "inheritedFrom": EXT_FOLDER, "role": "reader",
                                "inherited": True}],
         "emailAddress": TEST, "role": "reader", "deleted": False},
    ],
}


class TestOtherTenantSharedDriveOnRealShapes:
    """N4GOOGLE-02 / GDRIVE-03: a member of another tenant's shared drive.

    The drive is not in the admin listing, so its record group is a stub with no
    grants; the member's access has to be on the items.
    """

    @pytest.mark.asyncio
    async def test_the_member_reads_every_item(self) -> None:
        conn = _make_connector()
        conn._synced_drive_ids = {ADMIN_LISTED_DRIVE}
        updates = await _sync(conn, OTHER_TENANT_TREE, OTHER_TENANT_ACLS, as_user=VISHWJEET,
                              drive_id=OTHER_TENANT_DRIVE, is_shared_drive=True)
        stub_group: Set[str] = set()

        for file_id in OTHER_TENANT_TREE:
            assert VISHWJEET in _readers(updates, file_id, stub_group), file_id
        assert _stored_principals(updates[EXT_PDF]) == {ORGANIZER, VISHWJEET}

    @pytest.mark.asyncio
    async def test_a_folder_share_still_reaches_only_that_folder(self) -> None:
        conn = _make_connector()
        conn._synced_drive_ids = {ADMIN_LISTED_DRIVE}
        updates = await _sync(conn, OTHER_TENANT_TREE, OTHER_TENANT_ACLS, as_user=VISHWJEET,
                              drive_id=OTHER_TENANT_DRIVE, is_shared_drive=True)

        readable = {fid for fid in updates if TEST in _readers(updates, fid, set())}
        assert readable == {EXT_FOLDER, EXT_CHESS}
        assert TEST not in _stored_principals(updates[EXT_CHESS])

    @pytest.mark.asyncio
    async def test_an_admin_listed_drive_keeps_members_on_its_group(self) -> None:
        conn = _make_connector()
        conn._synced_drive_ids = {ADMIN_LISTED_DRIVE, OTHER_TENANT_DRIVE}
        updates = await _sync(conn, OTHER_TENANT_TREE, OTHER_TENANT_ACLS, as_user=VISHWJEET,
                              drive_id=OTHER_TENANT_DRIVE, is_shared_drive=True)

        assert _stored_principals(updates[EXT_PDF]) == set()
        assert _stored_principals(updates[EXT_FOLDER]) == {TEST}

    @pytest.mark.asyncio
    async def test_a_limited_folder_in_that_drive_does_not_keep_its_members(self) -> None:
        conn = _make_connector()
        conn._synced_drive_ids = {ADMIN_LISTED_DRIVE}
        limited = {EXT_FOLDER: _ext_item(EXT_FOLDER, "Untitled folder", "application/vnd.google-apps.folder",
                                         OTHER_TENANT_DRIVE, limited=True)}
        updates = await _sync(conn, limited, OTHER_TENANT_ACLS, as_user=VISHWJEET,
                              drive_id=OTHER_TENANT_DRIVE, is_shared_drive=True)

        assert _stored_principals(updates[EXT_FOLDER]) == {TEST}
        assert updates[EXT_FOLDER].record.inherit_permissions is False


GANTT = "1iVYBRmuwKeMUZqF7npu58rHaDPQroDbV7hVkxr5Uyu0"
_DOMAIN_READER = {
    "id": "09046145022669542625", "type": "domain",
    "permissionDetails": [{"permissionType": "file", "role": "reader", "inherited": False}],
    "role": "reader", "allowFileDiscovery": False, "domain": "pipeshub.app",
}
DOMAIN_SHARE_TREE = {
    GANTT: {
        "mimeType": "application/vnd.google-apps.spreadsheet", "parents": ["1xNGpdVMTs2avsxRPC6ei04B9qPsJsXyN"],
        "shared": True, "owners": [{"emailAddress": VISHWJEET}], "id": GANTT, "name": "Gantt chart",
        "trashed": False, "createdTime": "2026-07-31T11:58:34.030Z", "modifiedTime": "2026-08-31T11:56:15.320Z",
        "inheritedPermissionsDisabled": False,
    },
}
WORKSPACE_DOMAINS = {"pipeshub.app", "pipeshubapp.com"}


def _app_user(email: str, *, active: bool = True):
    from app.models.entities import AppUser

    return AppUser(app_name=Connectors.GOOGLE_DRIVE_WORKSPACE, connector_id="c", source_user_id=email,
                   email=email, full_name=email, is_active=active)


class TestDomainShareOnRealShapes:
    """N4GOOGLE-03 / GDRIVE-02, R2-06: "anyone at pipeshub.app" reaches pipeshub.app users only,
    and "anyone at pipeshub.app with the link" (Gantt chart's real share) reaches no one by itself."""

    @pytest.mark.asyncio
    async def test_a_domain_link_share_grants_only_the_owner(self) -> None:
        conn = _make_connector()
        conn._workspace_domains = set(WORKSPACE_DOMAINS)
        updates = await _sync(conn, DOMAIN_SHARE_TREE, {GANTT: [_DOMAIN_READER, _VISHWJEET_OWNER]},
                              as_user=VISHWJEET, drive_id=MY_DRIVE)

        assert _stored_principals(updates[GANTT]) == {VISHWJEET}

    @pytest.mark.asyncio
    async def test_a_colleague_who_lists_a_domain_link_file_gets_it_alone(self) -> None:
        conn = _make_connector()
        conn._workspace_domains = set(WORKSPACE_DOMAINS)
        updates = await _sync(conn, DOMAIN_SHARE_TREE, {GANTT: [_DOMAIN_READER, _VISHWJEET_OWNER]},
                              as_user=TEST, drive_id=MY_DRIVE)

        grants = {(p.email, p.type.value) for p in updates[GANTT].new_permissions}
        assert grants == {(TEST, "READER"), (VISHWJEET, "OWNER")}

    @pytest.mark.asyncio
    async def test_a_discoverable_domain_share_is_a_grant_to_its_domain_group(self) -> None:
        conn = _make_connector()
        conn._workspace_domains = set(WORKSPACE_DOMAINS)
        discoverable = dict(_DOMAIN_READER, allowFileDiscovery=True)
        updates = await _sync(conn, DOMAIN_SHARE_TREE, {GANTT: [discoverable, _VISHWJEET_OWNER]},
                              as_user=VISHWJEET, drive_id=MY_DRIVE)

        grants = {(p.entity_type, p.external_id if p.entity_type == EntityType.GROUP else p.email, p.type.value)
                  for p in updates[GANTT].new_permissions}
        assert grants == {
            (EntityType.GROUP, "domain:pipeshub.app", "READER"),
            (EntityType.USER, VISHWJEET, "OWNER"),
        }

    @pytest.mark.asyncio
    async def test_a_domain_share_that_does_not_say_it_is_discoverable_grants_only_the_owner(self) -> None:
        conn = _make_connector()
        conn._workspace_domains = set(WORKSPACE_DOMAINS)
        unknown = {k: v for k, v in _DOMAIN_READER.items() if k != "allowFileDiscovery"}
        updates = await _sync(conn, DOMAIN_SHARE_TREE, {GANTT: [unknown, _VISHWJEET_OWNER]},
                              as_user=VISHWJEET, drive_id=MY_DRIVE)

        assert _stored_principals(updates[GANTT]) == {VISHWJEET}

    @pytest.mark.asyncio
    async def test_permissions_list_asks_whether_a_share_is_discoverable(self) -> None:
        conn = _make_connector()
        source = _data_source({GANTT: [_DOMAIN_READER, _VISHWJEET_OWNER]})
        await conn._fetch_permissions(GANTT, user_email=VISHWJEET, drive_data_source=source)
        assert "allowFileDiscovery" in source.permissions_list.call_args.kwargs["fields"]

    @pytest.mark.asyncio
    async def test_a_share_with_another_domain_grants_nothing(self) -> None:
        conn = _make_connector()
        conn._workspace_domains = set(WORKSPACE_DOMAINS)
        partner = dict(_DOMAIN_READER, domain="partner.example")
        updates = await _sync(conn, DOMAIN_SHARE_TREE, {GANTT: [partner, _VISHWJEET_OWNER]},
                              as_user=VISHWJEET, drive_id=MY_DRIVE)

        assert _stored_principals(updates[GANTT]) == {VISHWJEET}

    @pytest.mark.asyncio
    async def test_each_workspace_domain_gets_a_group_of_its_active_users(self) -> None:
        conn = _make_connector()
        conn.data_entities_processor.on_new_user_groups = AsyncMock()
        conn.synced_users = [
            _app_user(TEST), _app_user(VISHWJEET), _app_user("noreply@pipeshubapp.com"),
            _app_user("gone@pipeshub.app", active=False),
        ]
        conn._workspace_domains = set(WORKSPACE_DOMAINS)

        await conn._sync_domain_groups()

        (groups,), _ = conn.data_entities_processor.on_new_user_groups.call_args
        members = {group.source_user_group_id: sorted(u.email for u in users) for group, users in groups}
        assert members == {
            "domain:pipeshub.app": sorted([TEST, VISHWJEET]),
            "domain:pipeshubapp.com": ["noreply@pipeshubapp.com"],
        }

    @pytest.mark.asyncio
    async def test_an_archived_or_suspended_user_is_not_in_the_domain_group(self) -> None:
        conn = _make_connector()
        conn.admin_data_source = MagicMock()
        conn.admin_data_source.users_list = AsyncMock(return_value={"users": [
            {"id": "1", "primaryEmail": TEST, "name": {"fullName": "t"}, "suspended": False, "archived": False},
            {"id": "2", "primaryEmail": VISHWJEET, "name": {"fullName": "v"}, "suspended": False},
            {"id": "3", "primaryEmail": "former@pipeshub.app", "name": {"fullName": "f"},
             "suspended": False, "archived": True},
            {"id": "4", "primaryEmail": "paused@pipeshub.app", "name": {"fullName": "p"}, "suspended": True},
        ]})
        conn.data_entities_processor.on_new_app_users = AsyncMock()
        conn.data_entities_processor.on_new_user_groups = AsyncMock()

        await conn._sync_users()
        await conn._sync_domain_groups()

        (groups,), _ = conn.data_entities_processor.on_new_user_groups.call_args
        members = {group.source_user_group_id: sorted(u.email for u in users) for group, users in groups}
        assert members == {"domain:pipeshub.app": sorted([TEST, VISHWJEET])}


SLACK_ALERTS = "1TGNrPj-0UEpfsPjldn9rO_ppieM0QFsbjoql1p_LhLE"
EXTERNAL_OWNER = "harshit.jaiswal@pipeshub.com"
SLACK_ALERTS_META = {
    "mimeType": "application/vnd.google-apps.spreadsheet", "shared": True,
    "owners": [{"emailAddress": EXTERNAL_OWNER}], "id": SLACK_ALERTS, "name": "Slack Alerts", "trashed": False,
    "createdTime": "2026-08-10T09:04:12.849Z", "modifiedTime": "2026-08-12T10:03:15.099Z",
    "sharedWithMeTime": "2026-08-12T10:03:15.103Z", "inheritedPermissionsDisabled": False,
}


def _insufficient_file_permissions():
    from googleapiclient.errors import HttpError

    resp = MagicMock()
    resp.status = 403
    error = HttpError(resp, b"forbidden")
    error.error_details = [{"reason": "insufficientFilePermissions"}]
    return error


class TestViewOnlyFileOnRealShapes:
    """N4GOOGLE-04 / GDRIVE-04: the viewer cannot list the ACL; the owner must still be granted."""

    async def _process(self, conn, permissions_list) -> object:
        source = MagicMock()
        source.permissions_list = permissions_list
        return await conn._process_drive_item(
            dict(SLACK_ALERTS_META), "perm-id", VISHWJEET, MY_DRIVE, drive_data_source=source,
        )

    @pytest.mark.asyncio
    async def test_a_refused_acl_read_keeps_the_viewer_and_adds_the_owner(self) -> None:
        conn = _make_connector()
        update = await self._process(conn, AsyncMock(side_effect=_insufficient_file_permissions()))

        grants = {(p.email, p.type.value) for p in update.new_permissions}
        assert grants == {(VISHWJEET, "READER"), (EXTERNAL_OWNER, "OWNER")}
        assert update.record.inherit_permissions is False
        assert EXTERNAL_OWNER in conn._external_emails

    @pytest.mark.asyncio
    async def test_an_empty_acl_answer_adds_the_owner_too(self) -> None:
        conn = _make_connector()
        update = await self._process(conn, AsyncMock(return_value={"permissions": []}))

        assert {p.email for p in update.new_permissions} == {VISHWJEET, EXTERNAL_OWNER}

    @pytest.mark.asyncio
    async def test_a_link_share_adds_the_owner_too(self) -> None:
        conn = _make_connector()
        link = {"id": "anyoneWithLink", "type": "anyone", "role": "reader"}
        update = await self._process(conn, AsyncMock(return_value={"permissions": [link]}))

        assert {p.email for p in update.new_permissions} == {VISHWJEET, EXTERNAL_OWNER}

    @pytest.mark.asyncio
    async def test_an_existing_record_gets_the_owner_added(self) -> None:
        conn = _make_connector()
        existing = MagicMock(id="rec-1", record_name="Slack Alerts", external_revision_id=None,
                             parent_external_record_id=None, external_record_group_id=None, version=0)
        conn.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        await self._process(conn, AsyncMock(side_effect=_insufficient_file_permissions()))

        (_, added), _ = conn.data_entities_processor.add_permission_to_record.call_args
        assert {p.email for p in added} == {VISHWJEET, EXTERNAL_OWNER}


class TestRecordOpenOfAnUnreadableFile:
    """N4GOOGLE-07: Drive's 404 also means "this user may not read it"; the message must not claim deletion."""

    @pytest.mark.asyncio
    async def test_a_404_does_not_say_the_item_no_longer_exists(self) -> None:
        from fastapi import HTTPException
        from googleapiclient.errors import HttpError

        conn = _make_connector()
        resp = MagicMock()
        resp.status = 404
        service = MagicMock()
        service.files.return_value.get.return_value.execute.side_effect = HttpError(resp, b"File not found")
        source = MagicMock()
        source.execute = AsyncMock(side_effect=lambda op: op())

        with pytest.raises(HTTPException) as raised:
            await conn._get_file_metadata_from_drive(BOX_TXT, service, source)

        assert raised.value.status_code == 404
        assert "no longer exists" not in raised.value.detail
        assert "access" in raised.value.detail


class TestSharedWithMeListsOnlyTheSharedItem:
    """N4GOOGLE-06 (shared drive half): a folder share puts the folder, not its subtree, in Shared with Me."""

    @pytest.mark.asyncio
    async def test_only_the_shared_folder_joins_the_users_shared_with_me_group(self) -> None:
        conn = _make_connector()
        conn._synced_drive_ids = {OTHER_TENANT_DRIVE}
        updates = await _sync(conn, OTHER_TENANT_TREE, OTHER_TENANT_ACLS, as_user=VISHWJEET,
                              drive_id=OTHER_TENANT_DRIVE, is_shared_drive=True)

        assert updates[EXT_FOLDER].record.shared_with_me_record_group_ids == [f"0S:{TEST}"]
        assert updates[EXT_CHESS].record.shared_with_me_record_group_ids == []
