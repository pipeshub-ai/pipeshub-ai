"""Google Drive for Workspace sync, driven over a fake Drive + Admin API with the real Google client.

The connector signs in the way production does: a real service-account key signs
a domain-wide-delegation JWT per impersonated user, the fake token endpoint reads
the impersonated subject out of it, and every Drive call is answered as that user.
Our databases are in-memory fakes.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any, Optional

import pytest
from drive_world import DriveWorld
from fastapi.responses import StreamingResponse
from google_behaviour_fakes import (
    ApiRequest,
    FakeConfigService,
    FakeEntitiesProcessor,
    FakeGoogleHttp,
    FakeSyncPointStore,
)
from googleapiclient.errors import HttpError

from app.connectors.sources.google.drive.team.connector import GoogleDriveTeamConnector
from app.models.entities import User
from app.models.permission import EntityType, PermissionType

CONNECTOR_ID = "drive-workspace-1"
ADMIN = "admin@example.com"
ALICE = "alice@example.com"
BOB = "bob@example.com"
_real_sleep = asyncio.sleep


class Workspace:
    def __init__(
        self,
        http: FakeGoogleHttp,
        records: FakeEntitiesProcessor,
        sync_points: FakeSyncPointStore,
        service_account: dict[str, Any],
    ) -> None:
        self.http = http
        self.records = records
        self.sync_points = sync_points
        self.world = DriveWorld(http)
        self.world.admin_email = ADMIN
        for email in (ADMIN, ALICE, BOB):
            self.world.add_user(email)
        records.active_users = [User(id=f"u-{e.split('@')[0]}", email=e, is_active=True) for e in (ALICE, BOB)]
        self.config: dict[str, Any] = {
            "auth": {"serviceAccountJson": json.dumps(service_account), "adminEmail": ADMIN},
        }
        self.config_service = FakeConfigService(CONNECTOR_ID, self.config)
        self.connector: Optional[GoogleDriveTeamConnector] = None

    def filters(self, **sync_values: object) -> None:
        self.config["filters"] = {"sync": {"values": sync_values}}

    async def connector_(self) -> GoogleDriveTeamConnector:
        if self.connector is None:
            self.connector = GoogleDriveTeamConnector(
                logging.getLogger("drive-workspace-behaviour"),
                self.records,
                self.sync_points,
                self.config_service,
                CONNECTOR_ID,
                "team",
                "admin-user",
            )
            assert await self.connector.init()
        return self.connector

    async def sync(self) -> None:
        await (await self.connector_()).run_sync()

    def names(self) -> set[str]:
        return {r.record_name for r in self.records.records.values() if not r.is_placeholder}

    def grants(self, external_id: str) -> set[tuple[Optional[str], str, str]]:
        return {
            (p.email or p.external_id, p.entity_type.name, p.type.name)
            for p in self.records.permissions.get(external_id, [])
        }

    def user_checkpoint(self, email: str) -> Optional[str]:
        user_id = self.world.users[email].user_id
        value = self.sync_points.value(f"/users/{user_id}")
        return value.get("pageToken") if value else None

    def impersonated(self) -> set[str]:
        return {r.identity for r in self.http.requests if r.path.startswith("/drive/v3/")}


@pytest.fixture(autouse=True)
def no_pacing_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-user pause between sync batches is pacing, not behaviour."""

    async def _sleep(delay: float, *args: object, **kwargs: object) -> None:
        await _real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _sleep)


@pytest.fixture
async def ws(
    google_http: FakeGoogleHttp,
    records: FakeEntitiesProcessor,
    sync_points: FakeSyncPointStore,
    service_account_info: dict[str, Any],
) -> AsyncIterator[Workspace]:
    workspace = Workspace(google_http, records, sync_points, service_account_info)
    yield workspace
    if workspace.connector is not None:
        await workspace.connector.cleanup()
    assert not google_http.unrouted, google_http.unrouted


def reader(email: str) -> dict[str, Any]:
    return {"type": "user", "role": "reader", "emailAddress": email}


# --- identities, users and groups --------------------------------------------


async def test_each_users_drive_is_read_as_that_user_through_domain_wide_delegation(ws: Workspace) -> None:
    ws.world.add_item("a1", "alice.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("b1", "bob.txt", parent="root-bob", owner=BOB)

    await ws.sync()

    assert ws.names() == {"alice.txt", "bob.txt"}
    assert {ALICE, BOB, ADMIN} <= ws.impersonated()
    assert {ADMIN, ALICE, BOB} <= ws.http.impersonated_subjects()
    assert ws.grants("a1") == {(ALICE, "USER", "OWNER")}
    assert ws.grants("b1") == {(BOB, "USER", "OWNER")}


async def test_users_and_groups_are_read_to_the_last_page(ws: Workspace) -> None:
    for n in range(3):
        ws.world.add_user(f"extra{n}@example.com")
    ws.world.add_group("eng@example.com", [ALICE, BOB, "extra0@example.com", "extra1@example.com", "extra2@example.com"])
    ws.world.add_group("ops@example.com", [BOB])
    ws.world.add_group("empty-ish@example.com", [ALICE])

    await ws.sync()

    assert set(ws.records.app_users) == {ADMIN, ALICE, BOB, "extra0@example.com", "extra1@example.com", "extra2@example.com"}
    assert {m.email for m in ws.records.user_groups["eng@example.com"][1]} == {
        ALICE, BOB, "extra0@example.com", "extra1@example.com", "extra2@example.com"
    }
    assert set(ws.records.user_groups) == {"eng@example.com", "ops@example.com", "empty-ish@example.com"}


async def test_a_failed_group_member_read_leaves_the_stored_group_alone(ws: Workspace) -> None:
    ws.world.add_group("eng@example.com", [ALICE, BOB])
    await ws.sync()
    ws.http.fail("GET", "/admin/directory/v1/groups/eng@example.com/members", 500, "backendError")

    await ws.sync()

    assert {m.email for m in ws.records.user_groups["eng@example.com"][1]} == {ALICE, BOB}


@pytest.mark.xfail(
    strict=True,
    reason="A group whose last member was removed is skipped instead of being saved empty, so the "
    "people who left it keep the group's access to every file shared with it.",
)
async def test_removing_the_last_member_of_a_group_removes_their_group_access(ws: Workspace) -> None:
    ws.world.add_group("eng@example.com", [BOB])
    await ws.sync()
    ws.world.groups["eng@example.com"]["members"] = []

    await ws.sync()

    assert ws.records.user_groups["eng@example.com"][1] == []


async def test_directory_quota_errors_are_retried_with_backoff(ws: Workspace, backoff_sleeps: list[float]) -> None:
    ws.http.fail("GET", "/admin/directory/v1/users", 429, "rateLimitExceeded", times=2)

    await ws.sync()

    assert ALICE in ws.records.app_users
    assert len(backoff_sleeps) == 2


async def test_a_user_whose_delegation_is_refused_does_not_stop_the_others(ws: Workspace) -> None:
    ws.world.add_item("a1", "alice.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("b1", "bob.txt", parent="root-bob", owner=BOB)
    ws.http.refused_subjects.add(ALICE)

    await ws.sync()

    assert ws.names() == {"bob.txt"}
    assert ws.user_checkpoint(ALICE) is None
    assert ws.user_checkpoint(BOB) is not None


async def test_only_users_active_in_pipeshub_are_synced(ws: Workspace) -> None:
    ws.world.add_item("a1", "alice.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("b1", "bob.txt", parent="root-bob", owner=BOB)
    ws.records.active_users = [u for u in ws.records.active_users if u.email == ALICE]

    await ws.sync()

    assert ws.names() == {"alice.txt"}
    assert BOB not in {r.identity for r in ws.http.calls("GET", "/drive/v3/(files|changes)")}


# --- sharing ------------------------------------------------------------------


async def test_user_group_domain_and_link_sharing_is_mapped(ws: Workspace) -> None:
    ws.world.add_group("eng@example.com", [BOB])
    ws.world.add_item("shared", "plan.txt", parent="root-alice", owner=ALICE, perms=[
        {"type": "user", "role": "writer", "emailAddress": BOB},
        {"type": "user", "role": "commenter", "emailAddress": "outsider@other.org"},
        {"type": "group", "role": "reader", "emailAddress": "eng@example.com"},
        {"type": "domain", "role": "reader", "domain": "example.com"},
    ])
    ws.world.add_item("linked", "open.txt", parent="root-alice", owner=ALICE, perms=[
        {"type": "user", "role": "writer", "emailAddress": BOB},
        {"type": "anyone", "role": "reader", "allowFileDiscovery": False},
    ])

    await ws.sync()

    grants = ws.grants("shared")
    assert {(ALICE, "USER", "OWNER"), (BOB, "USER", "WRITE"), ("outsider@other.org", "USER", "COMMENT"), ("eng@example.com", "GROUP", "READ")} <= grants
    assert [g for g in grants if g[1] == "DOMAIN"], grants
    linked = ws.grants("linked")
    assert {(ALICE, "USER", "OWNER"), (BOB, "USER", "WRITE")} <= linked
    assert {g[1] for g in linked} == {"USER", "ANYONE"}


async def test_a_file_shared_with_a_colleague_is_filed_under_their_shared_with_me(ws: Workspace) -> None:
    ws.world.add_item("shared", "plan.txt", parent="root-alice", owner=ALICE, perms=[reader(BOB)])

    await ws.sync()

    record = ws.records.records["shared"]
    assert (BOB, "USER", "READ") in ws.grants("shared")
    assert "0S:bob@example.com" in record.shared_with_me_record_group_ids
    assert ws.records.record_group_permissions["0S:bob@example.com"][0].email == BOB


async def test_shared_drives_become_record_groups_with_their_members(ws: Workspace) -> None:
    ws.world.add_group("eng@example.com", [BOB])
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer", "eng@example.com": "reader", BOB: "writer"})
    ws.world.add_item("sd-f1", "spec.txt", parent="sd-1")
    ws.world.add_item("sd-f2", "notes.txt", parent="sd-1")
    ws.world.add_item("sd-f3", "more.txt", parent="sd-1")

    await ws.sync()

    assert ws.records.record_groups["sd-1"].name == "Engineering"
    drive_grants = {(p.email or p.external_id, p.entity_type, p.type) for p in ws.records.record_group_permissions["sd-1"]}
    assert (ALICE, EntityType.USER, PermissionType.OWNER) in drive_grants
    assert ("eng@example.com", EntityType.GROUP, PermissionType.READ) in drive_grants
    assert {"spec.txt", "notes.txt", "more.txt"} <= ws.names()
    assert ws.records.records["sd-f1"].external_record_group_id == "sd-1"


async def test_a_shared_drive_page_quota_error_is_retried(ws: Workspace, backoff_sleeps: list[float]) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    for n in range(3):
        ws.world.add_item(f"sd-f{n}", f"f{n}.txt", parent="sd-1")
    ws.http.fail("GET", "/drive/v3/files", 403, "userRateLimitExceeded", times=1, when=lambda r: r.query.get("driveId") == "sd-1")

    await ws.sync()

    assert {"f0.txt", "f1.txt", "f2.txt"} <= ws.names()
    assert backoff_sleeps


async def test_a_file_shared_out_of_a_drive_the_user_is_not_a_member_of_is_synced_for_them(ws: Workspace) -> None:
    ws.world.add_user("carol@example.com")
    ws.world.add_drive("sd-x", "Carol's team", {"carol@example.com": "organizer"})
    ws.world.folder("sd-x-dir", "Handbook", parent="sd-x", perms=[reader(BOB)])
    ws.world.add_item("sd-x-page", "chapter-1.txt", parent="sd-x-dir")
    ws.world.add_item("sd-x-private", "budget.txt", parent="sd-x")

    await ws.sync()

    assert {"Handbook", "chapter-1.txt"} <= ws.names()
    assert "budget.txt" not in ws.names()
    assert "0S:bob@example.com" in ws.records.records["sd-x-page"].shared_with_me_record_group_ids
    assert BOB in ws.records.perm_emails("sd-x-page")


async def test_the_shared_drive_filter_skips_excluded_drives(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    ws.world.add_drive("sd-2", "Finance", {ALICE: "organizer"})
    ws.world.add_item("e1", "eng.txt", parent="sd-1")
    ws.world.add_item("f1", "fin.txt", parent="sd-2")
    ws.filters(drive_ids={"operator": "not_in", "type": "list", "value": ["sd-2"]})

    await ws.sync()

    assert "eng.txt" in ws.names()
    assert "fin.txt" not in ws.names()
    assert "sd-2" not in ws.records.record_groups


@pytest.mark.xfail(
    strict=True,
    reason="When a shared drive's file listing fails part-way, the sync stops that drive but still "
    "saves its checkpoint, so the files on the pages it never read are skipped for good.",
)
async def test_a_failed_shared_drive_page_does_not_save_the_drive_checkpoint(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    for n in range(4):
        ws.world.add_item(f"sd-f{n}", f"f{n}.txt", parent="sd-1")
    ws.http.fail("GET", "/drive/v3/files", 500, "backendError", when=lambda r: r.query.get("driveId") == "sd-1" and bool(r.query.get("pageToken")))

    await ws.sync()
    ws.http.clear_faults()
    await ws.sync()

    assert {f"f{n}.txt" for n in range(4)} <= ws.names()


@pytest.mark.xfail(
    strict=True,
    reason="A failed permission read is treated as 'this file is shared with nobody', so the next "
    "change to the file replaces its stored access with an empty list and people lose access "
    "(the rule from #3521/#3528 is that a failed read keeps what is stored).",
)
async def test_a_failed_permission_read_keeps_the_stored_access(ws: Workspace) -> None:
    ws.world.add_item("shared", "plan.txt", parent="root-alice", owner=ALICE, perms=[reader(BOB)])
    await ws.sync()
    assert (BOB, "USER", "READ") in ws.grants("shared")

    ws.world.rename("shared", "plan-v2.txt")
    ws.http.fail("GET", "/drive/v3/files/shared/permissions", 500, "backendError")
    await ws.sync()

    assert ws.records.records["shared"].record_name == "plan-v2.txt"
    assert (BOB, "USER", "READ") in ws.grants("shared")


@pytest.mark.xfail(
    strict=True,
    reason="If the second page of a file's permissions fails, the first page is saved as the "
    "complete list, silently dropping everyone listed after it.",
)
async def test_a_permission_list_that_fails_on_a_later_page_is_not_saved_as_complete(ws: Workspace) -> None:
    ws.world.perm_page_size = 2
    ws.world.add_item("shared", "plan.txt", parent="root-alice", owner=ALICE,
                      perms=[reader(BOB), reader("carol@example.com"), reader("dan@example.com")])
    await ws.sync()
    assert {"carol@example.com", "dan@example.com"} <= ws.records.perm_emails("shared")

    ws.world.rename("shared", "plan-v2.txt")
    ws.http.fail("GET", "/drive/v3/files/shared/permissions", 500, "backendError", when=lambda r: bool(r.query.get("pageToken")))
    await ws.sync()

    assert {"carol@example.com", "dan@example.com"} <= ws.records.perm_emails("shared")


async def test_a_reader_who_cannot_list_sharing_still_gets_access_without_wiping_others(ws: Workspace) -> None:
    ws.world.add_drive("sd-x", "Other team", {"carol@example.com": "organizer"})
    ws.world.add_item("sd-x-f", "handbook.txt", parent="sd-x", perms=[reader(BOB)])
    ws.world.files["sd-x-f"].perm_access = "forbidden"

    await ws.sync()

    assert (BOB, "USER", "READ") in ws.grants("sd-x-f")


# --- change tracking ----------------------------------------------------------


async def test_incremental_sync_applies_renames_moves_and_new_files_per_user(ws: Workspace) -> None:
    ws.world.folder("a-dir", "Projects", parent="root-alice", owner=ALICE)
    ws.world.add_item("a1", "draft.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("b1", "bob.txt", parent="root-bob", owner=BOB)
    await ws.sync()
    alice_cp, bob_cp = ws.user_checkpoint(ALICE), ws.user_checkpoint(BOB)

    ws.world.rename("a1", "final.txt")
    ws.world.move("a1", "a-dir")
    ws.world.add_item("a2", "new.txt", parent="a-dir", owner=ALICE)
    await ws.sync()

    assert ws.names() == {"Projects", "final.txt", "new.txt", "bob.txt"}
    assert ws.records.records["a1"].parent_external_record_id == "a-dir"
    assert int(ws.user_checkpoint(ALICE)) > int(alice_cp)
    assert int(ws.user_checkpoint(BOB)) >= int(bob_cp)


async def test_losing_access_to_a_file_removes_only_that_users_access(ws: Workspace) -> None:
    ws.world.add_item("shared", "plan.txt", parent="root-alice", owner=ALICE, perms=[reader(BOB)])
    await ws.sync()

    ws.world.unshare("shared", BOB)
    await ws.sync()

    assert "shared" in ws.records.records
    assert BOB not in ws.records.perm_emails("shared")
    assert ALICE in ws.records.perm_emails("shared")


async def test_a_file_deleted_from_a_shared_drive_is_deleted(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    ws.world.add_item("sd-f1", "spec.txt", parent="sd-1")
    ws.world.add_item("sd-f2", "keep.txt", parent="sd-1")
    await ws.sync()

    ws.world.delete("sd-f1")
    ws.world.rename("sd-f2", "kept.txt")
    await ws.sync()

    assert "sd-f1" not in ws.records.records
    assert ws.records.records["sd-f2"].record_name == "kept.txt"


async def test_shared_drive_changes_are_applied_incrementally(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    ws.world.folder("sd-dir", "Specs", parent="sd-1")
    ws.world.add_item("sd-f1", "draft.txt", parent="sd-1")
    await ws.sync()

    ws.world.move("sd-f1", "sd-dir")
    ws.world.add_item("sd-f2", "new-spec.txt", parent="sd-dir")
    await ws.sync()

    assert ws.records.records["sd-f1"].parent_external_record_id == "sd-dir"
    assert ws.records.records["sd-f2"].external_record_group_id == "sd-1"


async def test_leaving_a_shared_drive_removes_access_through_it(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer", BOB: "reader"})
    ws.world.add_item("sd-f1", "spec.txt", parent="sd-1")
    await ws.sync()
    assert BOB in {p.email for p in ws.records.record_group_permissions["sd-1"]}

    del ws.world.drives["sd-1"]["members"][BOB]
    await ws.sync()

    assert BOB not in {p.email for p in ws.records.record_group_permissions["sd-1"]}
    assert ALICE in {p.email for p in ws.records.record_group_permissions["sd-1"]}


async def test_a_member_list_that_fails_on_a_later_page_keeps_the_stored_members(ws: Workspace) -> None:
    ws.world.perm_page_size = 1
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer", BOB: "reader"})
    await ws.sync()
    assert {ALICE, BOB} <= {p.email for p in ws.records.record_group_permissions["sd-1"]}

    ws.http.fail("GET", "/drive/v3/files/sd-1/permissions", 500, "backendError", when=lambda r: bool(r.query.get("pageToken")))
    await ws.sync()

    assert {ALICE, BOB} <= {p.email for p in ws.records.record_group_permissions["sd-1"]}


@pytest.mark.xfail(
    strict=True,
    reason="The workspace connector lists My Drive without excluding the trash, so files already in "
    "the trash are synced and searchable (the personal Drive connector excludes them).",
)
async def test_files_already_in_the_trash_are_not_synced(ws: Workspace) -> None:
    ws.world.add_item("a1", "keep.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("a2", "binned.txt", parent="root-alice", owner=ALICE)
    ws.world.trash("a2")

    await ws.sync()

    assert ws.names() == {"keep.txt"}


@pytest.mark.xfail(
    strict=True,
    reason="Moving a file to the trash is not treated as a deletion by the workspace connector, so "
    "trashed files stay searchable until they are emptied from the trash.",
)
async def test_a_file_moved_to_the_trash_is_removed(ws: Workspace) -> None:
    ws.world.add_item("a1", "binned.txt", parent="root-alice", owner=ALICE)
    await ws.sync()

    ws.world.trash("a1")
    await ws.sync()

    assert "a1" not in ws.records.records


async def test_a_failed_change_page_does_not_advance_the_users_checkpoint(ws: Workspace) -> None:
    await ws.sync()
    checkpoint = ws.user_checkpoint(ALICE)
    for n in range(4):
        ws.world.add_item(f"a{n}", f"a{n}.txt", parent="root-alice", owner=ALICE)
    ws.http.fail("GET", "/drive/v3/changes", 500, "backendError",
                 when=lambda r: r.identity == ALICE and ":" in r.query["pageToken"] and not r.query.get("driveId"))

    await ws.sync()
    assert ws.user_checkpoint(ALICE) == checkpoint

    ws.http.clear_faults()
    await ws.sync()
    assert {f"a{n}.txt" for n in range(4)} <= ws.names()


@pytest.mark.xfail(
    strict=True,
    reason="A database error while saving a changed file is logged and swallowed, and the user's "
    "checkpoint still moves past the change, so that update is lost for good.",
)
async def test_a_database_failure_on_a_change_keeps_the_users_checkpoint(ws: Workspace) -> None:
    ws.world.add_item("a1", "draft.txt", parent="root-alice", owner=ALICE)
    await ws.sync()
    checkpoint = ws.user_checkpoint(ALICE)
    ws.world.rename("a1", "final.txt")
    ws.records.fail_writes_for.add("a1")

    await ws.sync()

    assert ws.user_checkpoint(ALICE) == checkpoint


@pytest.mark.xfail(
    strict=True,
    reason="Full sync of a user's My Drive stops at the first empty page even when Drive sent a "
    "nextPageToken, then saves the checkpoint, so later pages are never synced.",
)
async def test_an_empty_page_with_a_next_token_does_not_end_a_users_full_sync(ws: Workspace) -> None:
    for n in range(4):
        ws.world.add_item(f"a{n}", f"a{n}.txt", parent="root-alice", owner=ALICE)
    ws.world.empty_page_at = 1

    await ws.sync()

    assert {f"a{n}.txt" for n in range(4)} <= ws.names()


async def test_one_bad_item_does_not_abort_the_users_sync(ws: Workspace) -> None:
    ws.world.add_item("a1", "good.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("a2", "bad.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("a3", "also-good.txt", parent="root-alice", owner=ALICE)
    ws.world.files["a2"].meta["modifiedTime"] = "garbage"

    await ws.sync()

    assert {"good.txt", "also-good.txt"} <= ws.names()
    assert "bad.txt" not in ws.names()
    assert ws.user_checkpoint(ALICE) is not None


# --- folder filter ------------------------------------------------------------


async def test_a_folder_only_one_user_can_list_is_expanded_for_everyone(ws: Workspace) -> None:
    ws.world.folder("pick", "Picked", parent="root-bob", owner=BOB)
    ws.world.folder("pick-sub", "Sub", parent="pick", owner=BOB)
    ws.world.add_item("deep", "deep.txt", parent="pick-sub", owner=BOB)
    ws.world.add_item("elsewhere", "elsewhere.txt", parent="root-bob", owner=BOB)
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick"]})

    await ws.sync()

    assert {"Picked", "Sub", "deep.txt"} <= ws.names()
    assert "elsewhere.txt" not in ws.names()


async def test_a_transient_error_resolving_a_selected_folder_fails_the_run(ws: Workspace) -> None:
    ws.world.folder("pick", "Picked", parent="root-alice", owner=ALICE)
    ws.world.folder("pick-sub", "Sub", parent="pick", owner=ALICE)
    ws.world.add_item("deep", "deep.txt", parent="pick-sub", owner=ALICE)
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick"]})
    ws.http.fail("GET", "/drive/v3/files/pick", 503, "backendError", times=4)

    with pytest.raises(HttpError):
        await ws.sync()
    assert ws.user_checkpoint(ALICE) is None

    await ws.sync()
    assert "deep.txt" in ws.names()


@pytest.mark.parametrize("reason", ["dailyLimitExceeded", pytest.param(["insufficientFilePermissions", "dailyLimitExceeded"], id="refusal+dailyLimit")])
async def test_a_daily_quota_403_on_a_selected_folder_fails_the_run_instead_of_narrowing_it(ws: Workspace, reason: str | list[str]) -> None:
    ws.world.folder("pick", "Picked", parent="root-alice", owner=ALICE)
    ws.world.folder("pick-sub", "Sub", parent="pick", owner=ALICE)
    ws.world.add_item("deep", "deep.txt", parent="pick-sub", owner=ALICE)
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick"]})
    ws.http.fail("GET", "/drive/v3/files/pick", 403, reason, times=1)

    with pytest.raises(HttpError):
        await ws.sync()
    assert ws.user_checkpoint(ALICE) is None

    await ws.sync()
    assert "deep.txt" in ws.names()


async def test_a_daily_quota_error_walking_a_shared_folder_keeps_the_users_checkpoint(ws: Workspace) -> None:
    ws.world.add_user("carol@example.com")
    ws.world.add_drive("sd-x", "Carol's team", {"carol@example.com": "organizer"})
    ws.world.folder("sd-x-dir", "Handbook", parent="sd-x", perms=[reader(BOB)])
    ws.world.add_item("sd-x-page", "chapter-1.txt", parent="sd-x-dir")
    ws.http.fail("GET", "/drive/v3/files", 403, "dailyLimitExceeded", times=1,
                 when=lambda r: r.identity == BOB and "in parents" in r.query.get("q", ""))

    await ws.sync()
    assert ws.user_checkpoint(BOB) is None

    await ws.sync()
    assert "chapter-1.txt" in ws.names()


@pytest.mark.parametrize("reason", ["sharingRateLimitExceeded", "someReasonDriveAddsLater", None, pytest.param(["insufficientFilePermissions", "dailyLimitExceeded"], id="refusal+dailyLimit")])
async def test_a_403_walking_a_shared_folder_that_is_not_a_known_refusal_keeps_the_users_checkpoint(ws: Workspace, reason: Optional[str | list[str]]) -> None:
    ws.world.add_user("carol@example.com")
    ws.world.add_drive("sd-x", "Carol's team", {"carol@example.com": "organizer"})
    ws.world.folder("sd-x-dir", "Handbook", parent="sd-x", perms=[reader(BOB)])
    ws.world.add_item("sd-x-page", "chapter-1.txt", parent="sd-x-dir")
    ws.http.fail("GET", "/drive/v3/files", 403, reason, times=1,
                 when=lambda r: r.identity == BOB and "in parents" in r.query.get("q", ""))

    await ws.sync()
    assert ws.user_checkpoint(BOB) is None

    await ws.sync()
    assert "chapter-1.txt" in ws.names()


async def test_a_shared_folder_whose_access_was_refused_mid_walk_is_skipped_for_that_user(ws: Workspace) -> None:
    ws.world.add_user("carol@example.com")
    ws.world.add_drive("sd-x", "Carol's team", {"carol@example.com": "organizer"})
    ws.world.folder("sd-x-dir", "Handbook", parent="sd-x", perms=[reader(BOB)])
    ws.world.add_item("sd-x-page", "chapter-1.txt", parent="sd-x-dir")
    ws.http.fail("GET", "/drive/v3/files", 403, "insufficientFilePermissions",
                 when=lambda r: r.identity == BOB and "in parents" in r.query.get("q", ""))

    await ws.sync()

    assert "Handbook" in ws.names()
    assert "chapter-1.txt" not in ws.names()
    assert ws.user_checkpoint(BOB) is not None


def bob_shared_folder(ws: Workspace) -> None:
    ws.world.add_user("carol@example.com")
    ws.world.add_drive("sd-x", "Carol's team", {"carol@example.com": "organizer"})
    ws.world.folder("sd-x-dir", "Handbook", parent="sd-x", perms=[reader(BOB)])
    ws.world.add_item("sd-x-page", "chapter-1.txt", parent="sd-x-dir")
    ws.world.add_item("b1", "bob.txt", parent="root-bob", owner=BOB)
    ws.world.add_item("a1", "alice.txt", parent="root-alice", owner=ALICE)


def bob_walking(folder_id: str) -> Callable[[ApiRequest], bool]:
    return lambda r: r.identity == BOB and f"'{folder_id}' in parents" in r.query.get("q", "")


def user_sync_point(ws: Workspace, email: str) -> dict[str, Any]:
    return ws.sync_points.value(f"/users/{ws.world.users[email].user_id}") or {}


@pytest.mark.parametrize("reason", ["someReasonDriveAddsLater", None])
async def test_a_shared_folder_refused_without_a_known_reason_is_skipped_for_that_user_on_the_fifth_run(
    ws: Workspace, caplog: pytest.LogCaptureFixture, reason: str | None
) -> None:
    bob_shared_folder(ws)
    ws.http.fail("GET", "/drive/v3/files", 403, reason, when=bob_walking("sd-x-dir"))

    for _ in range(4):
        await ws.sync()
        assert ws.user_checkpoint(BOB) is None
    assert ws.user_checkpoint(ALICE) is not None

    await ws.sync()

    assert ws.user_checkpoint(BOB) is not None
    assert {"alice.txt", "bob.txt", "Handbook"} <= ws.names()
    assert "chapter-1.txt" not in ws.names()
    stored = user_sync_point(ws, BOB)
    assert stored["skippedSharedFolders"] == ["sd-x-dir"]
    assert stored["heldSharedFolders"] == []
    assert "Skipping the contents of shared folder sd-x-dir" in caplog.text

    ws.world.rename("b1", "bob-renamed.txt")
    await ws.sync()
    assert "bob-renamed.txt" in ws.names(), "bob's change feed runs once his checkpoint is saved"


async def test_two_shared_folders_refused_without_a_known_reason_use_their_five_runs_together(ws: Workspace) -> None:
    bob_shared_folder(ws)
    ws.world.folder("sd-x-dir-2", "Policies", parent="sd-x", perms=[reader(BOB)])
    ws.world.add_item("sd-x-page-2", "policy-1.txt", parent="sd-x-dir-2")
    for folder_id in ("sd-x-dir", "sd-x-dir-2"):
        ws.http.fail("GET", "/drive/v3/files", 403, "someReasonDriveAddsLater", when=bob_walking(folder_id))

    runs = 0
    while ws.user_checkpoint(BOB) is None and runs < 30:
        await ws.sync()
        runs += 1

    assert runs == 5
    stored = user_sync_point(ws, BOB)
    assert stored["skippedSharedFolders"] == ["sd-x-dir", "sd-x-dir-2"]
    assert stored["heldSharedFolders"] == []
    assert {"Handbook", "Policies", "bob.txt"} <= ws.names()


async def test_a_shared_folder_that_recovers_on_the_third_run_is_synced_for_that_user_and_its_count_cleared(ws: Workspace) -> None:
    bob_shared_folder(ws)
    ws.http.fail("GET", "/drive/v3/files", 403, "someReasonDriveAddsLater", times=2, when=bob_walking("sd-x-dir"))

    for _ in range(2):
        await ws.sync()
    assert user_sync_point(ws, BOB)["heldSharedFolders"] == ["sd-x-dir:2"]

    await ws.sync()

    assert "chapter-1.txt" in ws.names()
    assert ws.user_checkpoint(BOB) is not None
    assert user_sync_point(ws, BOB)["heldSharedFolders"] == []


def two_selected_folders(ws: Workspace) -> None:
    ws.world.folder("pick", "Picked", parent="root-alice", owner=ALICE)
    ws.world.folder("pick-sub", "Sub", parent="pick", owner=ALICE)
    ws.world.add_item("deep", "deep.txt", parent="pick-sub", owner=ALICE)
    ws.world.folder("other", "Other", parent="root-bob", owner=BOB)
    ws.world.folder("other-sub", "Other sub", parent="other", owner=BOB)
    ws.world.add_item("other-deep", "other-deep.txt", parent="other-sub", owner=BOB)
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick", "other"]})


def folder_filter_runs(ws: Workspace) -> list[str] | None:
    return (ws.sync_points.value("/folder_filter") or {}).get("heldFilterFolders")


@pytest.mark.parametrize("reason", ["someReasonDriveAddsLater", None])
async def test_a_selected_folder_refused_without_a_known_reason_is_left_out_for_everyone_on_the_fifth_run(
    ws: Workspace, caplog: pytest.LogCaptureFixture, reason: str | None
) -> None:
    two_selected_folders(ws)
    ws.http.fail("GET", "/drive/v3/files/pick", 403, reason)

    for _ in range(4):
        with pytest.raises(HttpError):
            await ws.sync()
        assert ws.user_checkpoint(ALICE) is None
        assert ws.user_checkpoint(BOB) is None

    await ws.sync()

    assert ws.user_checkpoint(ALICE) is not None
    assert ws.user_checkpoint(BOB) is not None
    assert {"Other", "Other sub", "other-deep.txt"} <= ws.names()
    assert "deep.txt" not in ws.names()
    assert folder_filter_runs(ws) == ["pick:5"]
    assert "Leaving folder pick out of the folder filter" in caplog.text

    await ws.sync()
    assert folder_filter_runs(ws) == ["pick:5"], "a folder already given up on does not fail later runs"


async def test_a_selected_folder_that_recovers_on_the_third_run_is_synced_and_its_count_cleared(ws: Workspace) -> None:
    two_selected_folders(ws)
    refusing = {"on": True}
    ws.http.fail("GET", "/drive/v3/files/pick", 403, "someReasonDriveAddsLater", when=lambda r: refusing["on"])

    for _ in range(2):
        with pytest.raises(HttpError):
            await ws.sync()
    assert folder_filter_runs(ws) == ["pick:2"]

    refusing["on"] = False
    await ws.sync()

    assert {"deep.txt", "other-deep.txt"} <= ws.names()
    assert folder_filter_runs(ws) == []


async def test_a_selected_folder_one_user_is_refused_without_a_reason_is_expanded_through_another(ws: Workspace) -> None:
    ws.world.folder("pick", "Picked", parent="root-bob", owner=BOB)
    ws.world.folder("pick-sub", "Sub", parent="pick", owner=BOB)
    ws.world.add_item("deep", "deep.txt", parent="pick-sub", owner=BOB)
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick"]})
    ws.http.fail("GET", "/drive/v3/files/pick", 403, "someReasonDriveAddsLater", when=lambda r: r.identity == ALICE)

    await ws.sync()

    assert "deep.txt" in ws.names()
    assert folder_filter_runs(ws) is None


async def test_a_selected_folder_every_user_is_refused_is_left_out_without_failing_the_run(ws: Workspace) -> None:
    two_selected_folders(ws)
    ws.http.fail("GET", "/drive/v3/files/pick", 403, "insufficientFilePermissions")

    await ws.sync()

    assert "other-deep.txt" in ws.names()
    assert "deep.txt" not in ws.names()
    assert ws.user_checkpoint(ALICE) is not None
    assert folder_filter_runs(ws) is None


# --- streaming and reindex ----------------------------------------------------


async def _body(response: StreamingResponse) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


async def test_streaming_uses_the_requesting_user_and_falls_back_past_refused_delegation(ws: Workspace) -> None:
    ws.world.add_item("shared", "plan.txt", parent="root-alice", owner=ALICE, perms=[reader(BOB)], content=b"secret plan")
    await ws.sync()
    connector = await ws.connector_()
    record = ws.records.records["shared"]

    response = await connector.stream_record(record, user_id="u-bob")
    assert await _body(response) == b"secret plan"
    assert ws.http.requests[-1].identity == BOB

    ws.http.refused_subjects.add(ALICE)
    response = await connector.stream_record(record)
    assert await _body(response) == b"secret plan"
    assert ws.http.requests[-1].identity == BOB


async def test_streaming_as_a_user_without_access_does_not_fall_back_to_someone_else(ws: Workspace) -> None:
    ws.world.add_item("private", "diary.txt", parent="root-alice", owner=ALICE)
    await ws.sync()
    connector = await ws.connector_()

    with pytest.raises(Exception) as raised:
        await connector.stream_record(ws.records.records["private"], user_id="u-bob")

    assert getattr(raised.value, "status_code", None) == 404
    assert {r.identity for r in ws.http.calls("GET", "/drive/v3/files/private")} == {BOB}


async def test_reindex_reads_the_file_as_a_user_who_can_see_it(ws: Workspace) -> None:
    ws.world.folder("dir", "Docs", parent="root-alice", owner=ALICE)
    ws.world.add_item("a2", "old.txt", parent="dir", owner=ALICE)
    await ws.sync()
    ws.world.rename("a2", "renamed.txt")
    connector = await ws.connector_()
    before = len(ws.http.requests)

    await connector.reindex_records([ws.records.records["a2"]])

    assert ws.records.records["a2"].record_name == "renamed.txt"
    assert {r.identity for r in ws.http.requests[before:] if r.path.startswith("/drive/")} == {ALICE}


@pytest.mark.xfail(
    strict=True,
    reason="Every file whose sharing can be read is reported as 'changed' (its permissions are always "
    "marked as changed), so reindex re-saves an unchanged, already-indexed file instead of asking "
    "for a reindex, and nothing is re-indexed.",
)
async def test_reindex_of_an_unchanged_indexed_file_asks_for_a_reindex(ws: Workspace) -> None:
    ws.world.folder("dir", "Docs", parent="root-alice", owner=ALICE)
    ws.world.add_item("a1", "same.txt", parent="dir", owner=ALICE)
    await ws.sync()
    connector = await ws.connector_()

    await connector.reindex_records([ws.records.records["a1"]])

    assert [r.external_record_id for r in ws.records.reindexed] == ["a1"]
