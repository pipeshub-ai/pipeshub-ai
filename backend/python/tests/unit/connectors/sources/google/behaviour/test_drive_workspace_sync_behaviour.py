"""Google Drive for Workspace sync, driven over a fake Drive + Admin API with the real Google client.

The connector signs in the way production does: a real service-account key signs
a domain-wide-delegation JWT per impersonated user, the fake token endpoint reads
the impersonated subject out of it, and every Drive call is answered as that user.
Our databases are in-memory fakes.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any, Optional

import pytest
from drive_world import FOLDER, DriveWorld, FileState
from fastapi.responses import StreamingResponse
from google_behaviour_fakes import (
    ApiRequest,
    FakeConfigService,
    FakeEntitiesProcessor,
    FakeGoogleHttp,
    FakeSyncPointStore,
)
from googleapiclient.errors import HttpError

from app.connectors.sources.google.common.connector_google_exceptions import (
    DriveSyncIncomplete,
    GoogleDriveError,
)
from app.connectors.sources.google.drive.team.connector import GoogleDriveTeamConnector
from app.models.entities import User
from app.models.permission import EntityType, PermissionType

CONNECTOR_ID = "drive-workspace-1"
ADMIN = "admin@example.com"
ALICE = "alice@example.com"
BOB = "bob@example.com"
PARTNER = "dana@partner.org"
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
        """One run. A run that left part of Drive unread ends with DriveSyncIncomplete, after doing everything
        else it could; that is recorded in `incomplete` rather than raised, so a test checks what the run did
        and, where it matters, that it was reported as incomplete."""
        try:
            await (await self.connector_()).run_sync()
            self.incomplete = False
        except DriveSyncIncomplete:
            self.incomplete = True

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


async def test_service_account_sign_in_uses_delegated_tokens_and_writes_no_settings(ws: Workspace) -> None:
    ws.world.add_item("a1", "alice.txt", parent="root-alice", owner=ALICE)

    await ws.sync()
    await ws.sync()

    assert ws.names() == {"alice.txt"}
    assert {t["grant_type"] for t in ws.http.token_requests} == {"urn:ietf:params:oauth:grant-type:jwt-bearer"}
    assert ws.config_service.writes == []
    assert "credentials" not in ws.config


async def test_the_admin_the_service_account_acts_as_is_registered_as_the_authenticated_account(ws: Workspace) -> None:
    await ws.sync()

    admin_id = ws.world.users[ADMIN].user_id
    assert ws.records.linked_authenticators == [(CONNECTOR_ID, "admin-user", ADMIN, admin_id)]
    assert ws.records.app_users[ADMIN].source_user_id == admin_id


async def test_an_admin_email_configured_as_an_alias_registers_the_primary_account(ws: Workspace) -> None:
    ws.world.aliases["it@example.com"] = ADMIN
    ws.config["auth"]["adminEmail"] = "it@example.com"

    await ws.sync()

    assert ws.records.linked_authenticators == [(CONNECTOR_ID, "admin-user", ADMIN, ws.world.users[ADMIN].user_id)]


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
    assert set(ws.records.user_groups) == {
        "eng@example.com", "ops@example.com", "empty-ish@example.com", "workspace-members", "domain-members:example.com"
    }


async def test_a_failed_group_member_read_leaves_the_stored_group_alone(ws: Workspace) -> None:
    ws.world.add_group("eng@example.com", [ALICE, BOB])
    await ws.sync()
    ws.http.fail("GET", "/admin/directory/v1/groups/eng@example.com/members", 500, "backendError")

    await ws.sync()

    assert {m.email for m in ws.records.user_groups["eng@example.com"][1]} == {ALICE, BOB}


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
    assert {(ALICE, "USER", "OWNER"), (BOB, "USER", "WRITE"), ("eng@example.com", "GROUP", "READ")} <= grants
    assert ("outsider@other.org", "USER", "READ") in grants, "a commenter is stored as a reader"
    assert {g[1] for g in grants} == {"USER", "GROUP"}, "domain sharing names nobody, so it is not stored as a grant"
    assert ws.grants("linked") == {(ALICE, "USER", "OWNER"), (BOB, "USER", "WRITE")}, (
        "link sharing names nobody, so it is not stored as a grant"
    )


def discoverable(kind: str, **extra: object) -> dict:
    return {"type": kind, "role": "reader", "allowFileDiscovery": True, **extra}


async def test_a_discoverable_domain_or_anyone_share_grants_the_enabled_people_of_the_audience(ws: Workspace) -> None:
    ws.world.add_item("f-domain", "pricing.txt", parent="root-alice", owner=ALICE, perms=[discoverable("domain", domain="example.com")])
    ws.world.add_item("f-anyone", "open.txt", parent="root-alice", owner=ALICE, perms=[discoverable("anyone")])
    ws.world.add_item("f-partner", "p.txt", parent="root-alice", owner=ALICE, perms=[discoverable("domain", domain="partner.com")])
    ws.world.add_item("f-link", "l.txt", parent="root-alice", owner=ALICE, perms=[
        {"type": "domain", "role": "reader", "domain": "example.com", "allowFileDiscovery": False},
        {"type": "anyone", "role": "reader", "allowFileDiscovery": False},
    ])
    await ws.sync()

    assert ("domain-members:example.com", "GROUP", "READ") in ws.grants("f-domain")
    assert ("workspace-members", "GROUP", "READ") in ws.grants("f-anyone")
    for external_id in ("f-partner", "f-link"):
        assert {g[1] for g in ws.grants(external_id)} == {"USER"}, external_id
    assert {m.email for m in ws.records.user_groups["workspace-members"][1]} == {ADMIN, ALICE, BOB}


def _forget_the_marker(ws: Workspace) -> None:
    """An install from before discoverable shares were stored: synced, with no marker."""
    for key in [k for k in ws.sync_points.sync_points if k.endswith("discoverable_shares_read")]:
        del ws.sync_points.sync_points[key]


def _share_without_a_change(ws: Workspace, file_id: str, share: dict) -> None:
    """A share the change feed never reports: only a full read of the file finds it."""
    ws.world.files[file_id].perms = [*ws.world.files[file_id].perms, share]


async def test_the_first_run_reads_every_drive_in_full_once_so_stored_shares_get_their_audience_grant(ws: Workspace) -> None:
    ws.world.add_item("f1", "a.txt", parent="root-alice", owner=ALICE)
    await ws.sync()
    _forget_the_marker(ws)
    _share_without_a_change(ws, "f1", discoverable("anyone"))

    await ws.sync()
    assert ("workspace-members", "GROUP", "READ") in ws.grants("f1"), "the checkpoints were cleared and the drive read again"
    marker_writes = [k for k, _ in ws.sync_points.writes if k.endswith("discoverable_shares_read")]
    assert len(marker_writes) == 2  # once on the first run, once on this one

    _share_without_a_change(ws, "f1", {"type": "user", "role": "reader", "emailAddress": BOB})
    await ws.sync()
    assert (BOB, "USER", "READ") not in ws.grants("f1"), "incremental again: an unreported change is not read"
    assert len([k for k, _ in ws.sync_points.writes if k.endswith("discoverable_shares_read")]) == 2


async def test_a_run_that_fails_after_the_marker_still_reads_in_full_next_time(ws: Workspace) -> None:
    ws.world.add_item("f1", "a.txt", parent="root-alice", owner=ALICE)
    await ws.sync()
    _forget_the_marker(ws)
    _share_without_a_change(ws, "f1", discoverable("anyone"))
    ws.http.fail("GET", "/drive/v3/files", 500, None, when=lambda r: r.identity == ALICE)

    await ws.sync()
    assert ws.incomplete is True
    assert ws.user_checkpoint(ALICE) is None, "the checkpoint stays cleared, so the next run starts from the beginning"

    ws.http.clear_faults()
    await ws.sync()
    assert ("workspace-members", "GROUP", "READ") in ws.grants("f1")


async def test_a_suspended_person_is_not_in_the_audience(ws: Workspace) -> None:
    ws.world.users[BOB].suspended = True
    await ws.sync()
    assert BOB not in {m.email for m in ws.records.user_groups["workspace-members"][1]}


async def test_a_members_only_drive_ignores_a_discoverable_share_and_a_domain_only_drive_keeps_it(ws: Workspace) -> None:
    ws.world.add_drive("sd-m", "Members", {ALICE: "organizer"}, MEMBERS_ONLY)
    ws.world.add_item("fm", "m.txt", parent="sd-m", perms=[discoverable("domain", domain="example.com")])
    ws.world.add_drive("sd-d", "Domain", {ALICE: "organizer"}, DOMAIN_ONLY)
    ws.world.add_item("fd", "d.txt", parent="sd-d", perms=[discoverable("domain", domain="example.com")])
    await ws.sync()
    assert "domain-members:example.com" not in {g[0] for g in ws.grants("fm")}
    assert "domain-members:example.com" in {g[0] for g in ws.grants("fd")}


async def test_a_domain_share_and_a_domain_link_share_grant_no_one(ws: Workspace) -> None:
    ws.records.active_users = [u for u in ws.records.active_users if u.email == ALICE]
    ws.world.folder("dir", "Partners", parent="root-alice", owner=ALICE, perms=[
        {"type": "domain", "role": "reader", "domain": "partner.com"},
    ])
    ws.world.add_item("f-domain", "pricing.txt", parent="root-alice", owner=ALICE, perms=[
        {"type": "domain", "role": "writer", "domain": "example.com"},
    ])
    ws.world.add_item("f-link", "roadmap.txt", parent="root-alice", owner=ALICE, perms=[
        {"type": "domain", "role": "reader", "domain": "example.com", "allowFileDiscovery": False},
    ])
    ws.world.add_item("f-later", "notes.txt", parent="root-alice", owner=ALICE)
    await ws.sync()
    ws.world.share("f-later", {"type": "domain", "role": "reader", "domain": "example.com"})
    await ws.sync()

    for external_id in ("dir", "f-domain", "f-link", "f-later"):
        assert ws.grants(external_id) == {(ALICE, "USER", "OWNER")}, (
            f"{external_id}: a share with a domain must not become a grant to the organization"
        )


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


async def test_a_deleted_shared_drive_takes_its_record_group_and_records_with_it(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    ws.world.add_item("sd-f1", "spec.txt", parent="sd-1")
    ws.world.add_item("sd-f2", "notes.txt", parent="sd-1")
    await ws.sync()
    assert {"spec.txt", "notes.txt"} <= ws.names()

    # Deleting a shared drive emits no change for its items; it just stops being listed.
    ws.world.drives.pop("sd-1")
    for file_id in ("sd-f1", "sd-f2"):
        ws.world.files.pop(file_id)
    await ws.sync()

    assert "sd-1" not in ws.records.record_groups
    assert not {"spec.txt", "notes.txt"} & ws.names()


async def test_an_empty_drive_listing_does_not_remove_drives_that_still_exist(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    ws.world.add_item("sd-f1", "spec.txt", parent="sd-1")
    await ws.sync()

    ws.http.route("GET", "/drive/v3/drives", lambda _req: {"kind": "drive#driveList", "drives": []})
    await ws.sync()

    assert "sd-1" not in ws.records.deleted_groups
    assert "spec.txt" in ws.names()


async def test_a_shared_drive_filtered_out_is_not_treated_as_deleted(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    ws.world.add_item("sd-f1", "spec.txt", parent="sd-1")
    await ws.sync()

    ws.filters(drive_ids={"operator": "not_in", "type": "list", "value": ["sd-1"]})
    await ws.sync()

    assert "sd-1" not in ws.records.deleted_groups, "a drive that still exists belongs to the filter cleanup"


async def test_a_filtered_out_shared_drive_deleted_later_is_still_removed(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    ws.world.add_item("sd-f1", "spec.txt", parent="sd-1")
    await ws.sync()
    ws.filters(drive_ids={"operator": "not_in", "type": "list", "value": ["sd-1"]})
    await ws.sync()

    ws.world.drives.pop("sd-1")
    ws.world.files.pop("sd-f1")
    await ws.sync()

    assert "sd-1" not in ws.records.record_groups


async def test_a_shared_drive_page_quota_error_is_retried(ws: Workspace, backoff_sleeps: list[float]) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    for n in range(3):
        ws.world.add_item(f"sd-f{n}", f"f{n}.txt", parent="sd-1")
    ws.http.fail("GET", "/drive/v3/files", 403, "userRateLimitExceeded", times=1, when=lambda r: r.query.get("driveId") == "sd-1")

    await ws.sync()

    assert {"f0.txt", "f1.txt", "f2.txt"} <= ws.names()
    assert backoff_sleeps


def reaches(ws: Workspace, email: str, external_id: str) -> bool:
    """Whether a user grant on the item, or on what it inherits from (its parents up to the top, then the top's
    record group, as a shared drive's members), names the person."""
    record = ws.records.records.get(external_id)
    while record is not None:
        if email in ws.records.perm_emails(record.external_record_id):
            return True
        if not record.inherit_permissions:
            return False
        parent = ws.records.records.get(record.parent_external_record_id or "")
        if parent is None:
            members = ws.records.record_group_permissions.get(record.external_record_group_id or "", [])
            return any(getattr(p, "email", None) == email for p in members)
        record = parent
    return False


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
    # Through the folder's grant, as in Drive: the page's own list names him only as inherited.
    assert reaches(ws, BOB, "sd-x-page")


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


async def test_a_failed_shared_drive_page_does_not_save_the_drive_checkpoint(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    for n in range(4):
        ws.world.add_item(f"sd-f{n}", f"f{n}.txt", parent="sd-1")
    ws.http.fail("GET", "/drive/v3/files", 500, "backendError", when=lambda r: r.query.get("driveId") == "sd-1" and bool(r.query.get("pageToken")))

    await ws.sync()
    ws.http.clear_faults()
    await ws.sync()

    assert {f"f{n}.txt" for n in range(4)} <= ws.names()


async def test_an_incomplete_shared_drive_listing_saves_no_drive_checkpoint(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    for n in range(4):
        ws.world.add_item(f"sd-f{n}", f"f{n}.txt", parent="sd-1")
    ws.world.incomplete_search_when = lambda r: r.query.get("driveId") == "sd-1"

    await ws.sync()

    assert {f"f{n}.txt" for n in range(4)} <= ws.names()
    assert not any(key.endswith("_sd-1") for key in ws.sync_points.sync_points)
    ws.world.incomplete_search_when = None
    await ws.sync()
    assert any(key.endswith("_sd-1") for key in ws.sync_points.sync_points)


async def test_an_incomplete_user_listing_saves_no_user_checkpoint(ws: Workspace) -> None:
    ws.world.add_item("a0", "alice-0.txt", parent="root-alice", owner=ALICE)
    ws.world.incomplete_search_when = lambda r: (
        r.identity == ALICE and not r.query.get("driveId") and "sharedWithMe" not in r.query.get("q", "")
    )

    await ws.sync()

    assert "alice-0.txt" in ws.names()
    assert ws.user_checkpoint(ALICE) is None
    ws.world.incomplete_search_when = None
    await ws.sync()
    assert ws.user_checkpoint(ALICE) is not None


async def test_an_incomplete_shared_with_me_listing_saves_no_user_checkpoint(ws: Workspace) -> None:
    ws.world.add_item("a0", "alice-0.txt", parent="root-alice", owner=ALICE)
    ws.world.incomplete_search_when = lambda r: (
        r.identity == ALICE and "sharedWithMe" in r.query.get("q", "")
    )

    await ws.sync()

    assert "alice-0.txt" in ws.names()
    assert ws.user_checkpoint(ALICE) is None


async def test_a_shared_drive_that_fails_part_way_through_a_full_resync_keeps_its_records(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    for n in range(4):
        ws.world.add_item(f"sd-f{n}", f"f{n}.txt", parent="sd-1")
    for n in range(6):
        ws.world.add_item(f"a{n}", f"alice-{n}.txt", parent="root-alice", owner=ALICE)
    await ws.sync()
    before = ws.names()
    ws.sync_points.sync_points.clear()
    ws.http.fail("GET", "/drive/v3/files", 500, "backendError", when=lambda r: r.query.get("driveId") == "sd-1" and bool(r.query.get("pageToken")))

    await ws.sync()

    assert ws.names() == before


async def test_a_user_whose_sync_fails_on_a_full_resync_keeps_their_records(ws: Workspace) -> None:
    for n in range(2):
        ws.world.add_item(f"a{n}", f"alice-{n}.txt", parent="root-alice", owner=ALICE)
    for n in range(4):
        ws.world.add_item(f"b{n}", f"bob-{n}.txt", parent="root-bob", owner=BOB)
    await ws.sync()
    before = ws.names()
    ws.sync_points.sync_points.clear()
    ws.http.refused_subjects.add(ALICE)

    await ws.sync()

    assert ws.names() == before


async def test_a_failed_permission_read_keeps_the_stored_access(ws: Workspace) -> None:
    ws.world.add_item("shared", "plan.txt", parent="root-alice", owner=ALICE, perms=[reader(BOB)])
    await ws.sync()
    assert (BOB, "USER", "READ") in ws.grants("shared")

    ws.world.rename("shared", "plan-v2.txt")
    ws.http.fail("GET", "/drive/v3/files/shared/permissions", 500, "backendError")
    await ws.sync()

    assert (BOB, "USER", "READ") in ws.grants("shared")
    assert ws.incomplete, "a change whose permissions could not be read is not reported as synced"

    ws.http.clear_faults()
    await ws.sync()
    assert ws.records.records["shared"].record_name == "plan-v2.txt", "the change is read again"
    assert (BOB, "USER", "READ") in ws.grants("shared")


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


async def test_a_file_deleted_from_my_drive_is_deleted_for_everyone(ws: Workspace) -> None:
    ws.world.add_item("shared", "plan.txt", parent="root-alice", owner=ALICE, perms=[reader(BOB)])
    ws.world.add_item("kept", "notes.txt", parent="root-alice", owner=ALICE)
    await ws.sync()
    record_id = ws.records.records["shared"].id

    ws.world.delete("shared")
    await ws.sync()

    assert "shared" not in ws.records.records
    assert record_id in ws.records.deleted
    assert "kept" in ws.records.records


async def test_a_folder_moved_to_the_trash_takes_its_files_along(ws: Workspace) -> None:
    ws.world.folder("dir", "Projects", parent="root-alice", owner=ALICE)
    ws.world.add_item("inside", "draft.txt", parent="dir", owner=ALICE)
    await ws.sync()

    ws.world.trash("dir")
    await ws.sync()

    assert "dir" not in ws.records.records
    assert "inside" not in ws.records.records


async def test_a_folder_already_in_our_trash_is_removed_when_the_source_trashes_it(ws: Workspace) -> None:
    ws.world.folder("dir", "Projects", parent="root-alice", owner=ALICE)
    ws.world.add_item("inside", "draft.txt", parent="dir", owner=ALICE)
    await ws.sync()
    ws.records.records["dir"].is_deleted = True

    ws.world.trash("dir")
    await ws.sync()

    assert "dir" not in ws.records.records, "the delete must take a folder that is in the trash"
    assert "inside" not in ws.records.records


async def test_a_delete_the_owner_cannot_confirm_yet_is_retried_next_sync(ws: Workspace) -> None:
    ws.world.add_item("a1", "plan.txt", parent="root-alice", owner=ALICE)
    await ws.sync()
    checkpoint = ws.user_checkpoint(ALICE)

    ws.world.delete("a1")
    ws.http.fail("GET", "/drive/v3/files/a1", 500, "backendError", when=lambda r: r.identity == ALICE)
    await ws.sync()

    assert "a1" in ws.records.records
    assert ws.records.deleted == []
    assert ws.user_checkpoint(ALICE) == checkpoint

    ws.http.clear_faults()
    await ws.sync()

    assert "a1" not in ws.records.records


async def test_a_delete_that_fails_to_save_is_retried_next_sync(ws: Workspace) -> None:
    ws.world.add_item("a1", "plan.txt", parent="root-alice", owner=ALICE)
    await ws.sync()
    checkpoint = ws.user_checkpoint(ALICE)

    ws.world.delete("a1")
    ws.records.fail_writes_for.add("a1")
    await ws.sync()

    assert "a1" in ws.records.records
    assert ws.user_checkpoint(ALICE) == checkpoint

    ws.records.fail_writes_for.discard("a1")
    await ws.sync()

    assert "a1" not in ws.records.records


async def test_an_owner_lookup_that_fails_holds_the_change_for_next_sync(ws: Workspace) -> None:
    ws.world.add_item("a1", "plan.txt", parent="root-alice", owner=ALICE, perms=[reader(BOB)])
    await ws.sync()
    checkpoints = ws.user_checkpoint(ALICE), ws.user_checkpoint(BOB)

    ws.world.delete("a1")
    ws.records.fail_owner_lookup = True
    await ws.sync()

    assert "a1" in ws.records.records
    assert BOB in ws.records.perm_emails("a1")
    assert (ws.user_checkpoint(ALICE), ws.user_checkpoint(BOB)) == checkpoints

    ws.records.fail_owner_lookup = False
    await ws.sync()

    assert "a1" not in ws.records.records


async def test_an_organizer_leaving_a_shared_drive_does_not_delete_its_files(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer", BOB: "organizer"})
    ws.world.add_item("sd-f1", "spec.txt", parent="sd-1", perms=[{"type": "user", "role": "organizer", "emailAddress": ALICE}])
    await ws.sync()
    assert "sd-f1" in ws.records.records

    def alice_leaves() -> None:
        del ws.world.drives["sd-1"]["members"][ALICE]
        ws.world.files["sd-f1"].perms = []

    ws.world._mutate("sd-f1", alice_leaves)
    await ws.sync()

    assert "sd-f1" in ws.records.records
    assert ws.records.deleted == []


async def test_a_file_from_a_filtered_out_drive_only_loses_the_removed_users_access(ws: Workspace) -> None:
    ws.world.add_drive("sd-2", "Finance", {ALICE: "organizer"})
    # A writer's view lists every permission, so the organizer is stored as an owner.
    ws.world.add_item("f1", "fin.txt", parent="sd-2", perms=[{"type": "user", "role": "writer", "emailAddress": BOB}])
    ws.filters(drive_ids={"operator": "not_in", "type": "list", "value": ["sd-2"]})
    await ws.sync()
    assert "f1" in ws.records.records
    assert ws.records.records["f1"].external_record_group_id is None
    assert ALICE in ws.records.perm_emails("f1"), "the organizer is stored as an owner"

    def alice_leaves() -> None:
        del ws.world.drives["sd-2"]["members"][ALICE]

    ws.world._mutate("f1", alice_leaves)
    await ws.sync()

    assert "f1" in ws.records.records, "an organizer leaving is not a delete for everyone"
    assert ws.records.deleted == []


def _file_in_filtered_out_drive(ws: Workspace) -> None:
    ws.world.add_drive("sd-2", "Finance", {ALICE: "organizer"})
    ws.world.add_item("f1", "fin.txt", parent="sd-2", perms=[{"type": "user", "role": "writer", "emailAddress": BOB}])
    ws.filters(drive_ids={"operator": "not_in", "type": "list", "value": ["sd-2"]})


async def test_a_file_deleted_from_a_filtered_out_drive_is_removed(ws: Workspace) -> None:
    _file_in_filtered_out_drive(ws)
    await ws.sync()
    assert "f1" in ws.records.records

    ws.world.delete("f1")
    await ws.sync()

    assert "f1" not in ws.records.records, "no drive sync walks this drive, so the removed change decides"


async def test_a_file_deleted_from_a_drive_no_synced_user_belongs_to_is_removed(ws: Workspace) -> None:
    ws.world.add_drive("sd-3", "Contractors", {"ghost@example.com": "organizer"})
    ws.world.add_item("g1", "brief.txt", parent="sd-3", perms=[{"type": "user", "role": "writer", "emailAddress": BOB}])
    await ws.sync()
    assert "g1" in ws.records.records

    ws.world.delete("g1")
    await ws.sync()

    assert "g1" not in ws.records.records, "no synced member walks this drive, so the removed change decides"


async def test_a_drive_whose_only_member_is_not_synced_by_this_deployment_is_not_walked(ws: Workspace) -> None:
    ws.records.active_users = [u for u in ws.records.active_users if u.email == ALICE]
    ws.world.add_drive("sd-5", "Legal", {BOB: "organizer"})
    ws.world.add_item("l1", "nda.txt", parent="sd-5", perms=[{"type": "user", "role": "writer", "emailAddress": ALICE}])
    await ws.sync()
    assert "l1" in ws.records.records

    ws.world.delete("l1")
    await ws.sync()

    assert "l1" not in ws.records.records, "Bob is in the Workspace but not synced here, so no sync walks the drive"


async def test_a_member_drive_file_one_user_loses_is_kept(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    ws.world.add_item("e1", "spec.txt", parent="sd-1", perms=[{"type": "user", "role": "writer", "emailAddress": BOB}])
    await ws.sync()

    ws.world._mutate("e1", lambda: setattr(ws.world.files["e1"], "perms", []))
    await ws.sync()

    assert "e1" in ws.records.records, "Alice still walks the drive, so Bob losing access removes only his"
    assert ws.records.deleted == []


async def test_a_drive_whose_synced_member_joins_through_a_group_still_walks_its_deletes(ws: Workspace) -> None:
    ws.world.add_group("eng@example.com", [ALICE])
    ws.world.add_drive("sd-4", "Platform", {"eng@example.com": "organizer"})
    ws.world.add_item("p1", "runbook.txt", parent="sd-4", perms=[{"type": "user", "role": "writer", "emailAddress": BOB}])
    await ws.sync()
    assert "p1" in ws.records.records

    ws.world._mutate("p1", lambda: setattr(ws.world.files["p1"], "perms", []))
    await ws.sync()

    assert "p1" in ws.records.records, "a group member walks the drive, so one lost share isn't a delete"


async def test_a_drive_whose_synced_member_is_in_a_nested_group_still_walks_its_deletes(ws: Workspace) -> None:
    ws.world.add_group("platform@example.com", [ALICE])
    ws.world.add_group("eng@example.com", ["platform@example.com"])
    ws.world.add_drive("sd-4", "Platform", {"eng@example.com": "organizer"})
    ws.world.add_item("p1", "runbook.txt", parent="sd-4", perms=[{"type": "user", "role": "writer", "emailAddress": BOB}])
    await ws.sync()
    assert "p1" in ws.records.records

    ws.world._mutate("p1", lambda: setattr(ws.world.files["p1"], "perms", []))
    await ws.sync()
    assert "p1" in ws.records.records, "Alice reaches the drive through a group inside a group"
    assert ws.records.deleted == []

    ws.world.delete("p1")
    await ws.sync()
    assert "p1" not in ws.records.records, "Alice's walk of the drive picks up the real delete"


def _file_shared_with_a_group_in_an_unwalked_drive(ws: Workspace, group_members: list[str]) -> None:
    ws.world.add_group("sales@example.com", group_members)
    ws.world.add_drive("sd-3", "Contractors", {"ghost@example.com": "organizer"})
    ws.world.add_item("g1", "brief.txt", parent="sd-3", perms=[
        {"type": "user", "role": "writer", "emailAddress": BOB},
        {"type": "group", "role": "reader", "emailAddress": "sales@example.com"},
    ])
    # Every reader sees the full sharing list, so nobody's sync stands in a direct edge for Alice.
    ws.world.files["g1"].perm_access = "all"


async def test_a_file_a_synced_user_still_opens_through_a_group_is_kept(ws: Workspace) -> None:
    _file_shared_with_a_group_in_an_unwalked_drive(ws, [ALICE])
    await ws.sync()
    assert ("sales@example.com", "GROUP", "READ") in ws.grants("g1")
    assert ALICE not in ws.records.perm_emails("g1"), "Alice's only access is the group"

    ws.world.unshare("g1", BOB)
    await ws.sync()

    assert "g1" in ws.records.records, "Alice can still open it through sales@, so it is not gone"
    assert ws.records.deleted == []


async def test_a_file_a_synced_user_still_opens_through_a_nested_group_is_kept(ws: Workspace) -> None:
    ws.world.add_group("emea@example.com", [ALICE])
    _file_shared_with_a_group_in_an_unwalked_drive(ws, ["emea@example.com"])
    await ws.sync()
    assert [m.email for m in ws.records.user_groups["sales@example.com"][1]] == [ALICE], \
        "sales@ holds Alice through emea@: Drive gives a nested group's members the outer group's access"
    assert ("sales@example.com", "GROUP", "READ") in ws.grants("g1")
    assert ALICE not in ws.records.perm_emails("g1"), "Alice's only access is sales@ through emea@"

    ws.world.unshare("g1", BOB)
    await ws.sync()

    assert "g1" in ws.records.records, "Alice is in sales@ through emea@, and can still open it"
    assert ws.records.deleted == []


def _audience_file_in_an_unwalked_drive(ws: Workspace, share: dict) -> None:
    """A file in a shared drive no synced user is a member of, shared with Bob and an audience."""
    ws.world.add_user("cara@example.com")
    ws.records.active_users = [*ws.records.active_users, User(id="u-cara", email="cara@example.com", is_active=True)]
    ws.world.add_drive("sd-3", "Contractors", {"ghost@example.com": "organizer"})
    ws.world.add_item("g1", "brief.txt", parent="sd-3", perms=[{"type": "user", "role": "writer", "emailAddress": BOB}, share])
    ws.world.files["g1"].perm_access = "all"


async def test_a_file_whose_audience_share_is_gone_is_removed_however_large_the_audience(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.connectors.sources.google.drive.team.connector as drive_connector

    # Fewer people may be asked than the audience holds: each has the same access, so their "no" is the answer.
    monkeypatch.setattr(drive_connector, "MAX_IMPERSONATION_CANDIDATES", 1)
    _audience_file_in_an_unwalked_drive(ws, discoverable("anyone"))
    await ws.sync()
    assert ("workspace-members", "GROUP", "READ") in ws.grants("g1")

    # The share goes without a change anyone's feed reports, and Bob loses access.
    ws.world.files["g1"].perms = [p for p in ws.world.files["g1"].perms if p.get("type") != "anyone"]
    ws.world.unshare("g1", BOB)
    ws.http.fail("GET", "/drive/v3/files/g1", 403, "driveServiceDisabled", when=lambda r: r.identity == ALICE)
    await ws.sync()

    assert "g1" not in ws.records.records, "nobody can open it: its audience grant must not outlive the share"


async def test_a_file_an_audience_still_opens_is_kept(ws: Workspace) -> None:
    _audience_file_in_an_unwalked_drive(ws, discoverable("anyone"))
    await ws.sync()
    ws.world.unshare("g1", BOB)
    await ws.sync()
    assert ("workspace-members", "GROUP", "READ") in ws.grants("g1")


async def test_a_file_a_domain_audience_still_opens_is_kept_though_more_people_elsewhere_cannot(ws: Workspace) -> None:
    ws.world.other_domains = {"labs.example.org": []}
    for email in ("a1@example.com", "a2@example.com", "a3@example.com", LABS):
        ws.world.add_user(email)
        ws.records.active_users = [*ws.records.active_users, User(id=f"u-{email}", email=email, is_active=True)]
    _audience_file_in_an_unwalked_drive(ws, discoverable("domain", domain="labs.example.org"))
    await ws.sync()
    assert ("domain-members:labs.example.org", "GROUP", "READ") in ws.grants("g1")

    ws.world.unshare("g1", BOB)
    await ws.sync()
    assert "g1" in ws.records.records, "Zed is on labs.example.org and is asked first"


async def test_a_file_none_of_whose_audience_answers_is_kept_and_asked_again(ws: Workspace) -> None:
    _audience_file_in_an_unwalked_drive(ws, discoverable("anyone"))
    await ws.sync()
    ws.world.files["g1"].perms = [p for p in ws.world.files["g1"].perms if p.get("type") != "anyone"]
    ws.world.unshare("g1", BOB)
    ws.http.fail("GET", "/drive/v3/files/g1", 500, "backendError", when=lambda r: r.identity != BOB)
    await ws.sync()
    assert "g1" in ws.records.records and ws.incomplete is True

    ws.http.clear_faults()
    await ws.sync()
    assert "g1" not in ws.records.records


async def test_a_file_shared_with_a_group_the_directory_wont_list_is_kept(ws: Workspace) -> None:
    _file_shared_with_a_group_in_an_unwalked_drive(ws, [ALICE])
    await ws.sync()
    checkpoint = ws.user_checkpoint(BOB)

    ws.world.unshare("g1", BOB)
    ws.http.fail("GET", "/admin/directory/v1/groups/sales@example.com/members", 403, "forbidden")
    await ws.sync()

    assert "g1" in ws.records.records, "members that can't be read are not taken for nobody"
    assert ws.records.deleted == []
    assert ws.user_checkpoint(BOB) != checkpoint, "a refusal that won't go away doesn't hold Bob's changes"


@pytest.mark.parametrize(
    ("status", "reason"),
    [(500, "backendError"), (403, "rateLimitExceeded"), (403, None), (403, "aReasonGoogleAddsLater")],
)
async def test_a_group_member_read_that_fails_holds_the_removed_change(
    ws: Workspace, backoff_sleeps: list[float], status: int, reason: str
) -> None:
    _file_shared_with_a_group_in_an_unwalked_drive(ws, [ALICE])
    await ws.sync()
    checkpoint = ws.user_checkpoint(BOB)

    ws.world.unshare("g1", BOB)
    ws.http.fail("GET", "/admin/directory/v1/groups/sales@example.com/members", status, reason)
    await ws.sync()
    assert "g1" in ws.records.records
    assert ws.user_checkpoint(BOB) == checkpoint

    ws.http.clear_faults()
    await ws.sync()
    assert "g1" in ws.records.records, "Alice can still open it through sales@"
    assert ws.user_checkpoint(BOB) != checkpoint
    assert ws.records.deleted == []


@pytest.mark.parametrize("reason", [None, "aReasonGoogleAddsLater"])
async def test_a_drive_member_group_read_refused_without_a_known_reason_holds_the_removed_change(
    ws: Workspace, backoff_sleeps: list[float], reason: str | None
) -> None:
    ws.world.add_group("eng@example.com", ["ghost@example.com"])
    ws.world.add_drive("sd-4", "Platform", {"eng@example.com": "organizer"})
    ws.world.add_item("p1", "runbook.txt", parent="sd-4", perms=[{"type": "user", "role": "writer", "emailAddress": BOB}])
    await ws.sync()
    assert "p1" in ws.records.records
    checkpoint = ws.user_checkpoint(BOB)

    ws.world.delete("p1")
    ws.http.fail("GET", "/admin/directory/v1/groups/eng@example.com/members", 403, reason)
    await ws.sync()
    assert "p1" in ws.records.records
    assert ws.user_checkpoint(BOB) == checkpoint, "a refusal that may clear is not taken for a drive member"

    ws.http.clear_faults()
    await ws.sync()
    assert "p1" not in ws.records.records, "nobody synced is in eng@, so the replayed change deletes it"


async def test_a_group_grant_lookup_that_fails_holds_the_removed_change(ws: Workspace) -> None:
    _file_shared_with_a_group_in_an_unwalked_drive(ws, [ALICE])
    await ws.sync()
    checkpoint = ws.user_checkpoint(BOB)

    ws.world.unshare("g1", BOB)
    ws.records.fail_group_permission_lookup = True
    await ws.sync()
    assert "g1" in ws.records.records
    assert ws.user_checkpoint(BOB) == checkpoint

    ws.records.fail_group_permission_lookup = False
    await ws.sync()
    assert "g1" in ws.records.records
    assert ws.records.deleted == []


async def test_a_permission_lookup_that_fails_holds_the_removed_change(ws: Workspace) -> None:
    _file_in_filtered_out_drive(ws)
    await ws.sync()
    checkpoints = ws.user_checkpoint(ALICE), ws.user_checkpoint(BOB)

    ws.world.delete("f1")
    ws.records.fail_permission_lookup = True
    await ws.sync()
    assert "f1" in ws.records.records
    assert (ws.user_checkpoint(ALICE), ws.user_checkpoint(BOB)) == checkpoints

    ws.records.fail_permission_lookup = False
    await ws.sync()
    assert "f1" not in ws.records.records


def alice_checking(r: ApiRequest) -> bool:
    return r.identity == ALICE and r.query.get("fields") == "id, trashed"


@pytest.mark.parametrize("reason", [None, "aReasonGoogleAddsLater"])
async def test_a_removed_change_refused_without_a_known_reason_drops_only_that_users_access_on_the_fifth_run(
    ws: Workspace, reason: str | None
) -> None:
    _file_in_filtered_out_drive(ws)
    await ws.sync()
    checkpoint = ws.user_checkpoint(BOB)

    ws.world.unshare("f1", BOB)
    ws.http.fail("GET", "/drive/v3/files/f1", 403, reason, when=alice_checking)
    for run in range(1, 5):
        await ws.sync()
        assert ws.user_checkpoint(BOB) == checkpoint
        assert BOB in ws.records.perm_emails("f1")
        assert user_sync_point(ws, BOB)["heldRemovedChanges"] == [f"f1:{run}"]

    await ws.sync()

    assert ws.user_checkpoint(BOB) != checkpoint, "Bob's changes move on after the fifth refusal"
    assert BOB not in ws.records.perm_emails("f1")
    assert ALICE in ws.records.perm_emails("f1")
    assert "f1" in ws.records.records, "giving up never deletes the file for everyone"
    assert ws.records.deleted == []
    assert user_sync_point(ws, BOB)["heldRemovedChanges"] == []


async def test_a_removed_change_whose_refusal_clears_before_the_limit_is_decided_normally(ws: Workspace) -> None:
    ws.world.add_item("a1", "plan.txt", parent="root-alice", owner=ALICE, perms=[reader(BOB)])
    await ws.sync()
    checkpoint = ws.user_checkpoint(BOB)

    ws.world.delete("a1")
    ws.http.fail("GET", "/drive/v3/files/a1", 403, "aReasonGoogleAddsLater", when=alice_checking)
    for _ in range(2):
        await ws.sync()
    assert "a1" in ws.records.records
    assert ws.user_checkpoint(BOB) == checkpoint
    assert user_sync_point(ws, BOB)["heldRemovedChanges"] == ["a1:2"]

    ws.http.clear_faults()
    await ws.sync()

    assert "a1" not in ws.records.records, "the owner now says it is gone, so it is deleted for everyone"
    assert ws.user_checkpoint(BOB) != checkpoint
    assert user_sync_point(ws, BOB)["heldRemovedChanges"] == []
    assert user_sync_point(ws, ALICE)["heldRemovedChanges"] == []


async def test_a_file_owned_outside_the_workspace_goes_once_no_synced_user_can_open_it(ws: Workspace) -> None:
    ws.world.files["ext-root"] = FileState(
        {"id": "ext-root", "name": "My Drive", "mimeType": FOLDER, "owners": [{"emailAddress": PARTNER}], "parents": []}
    )
    ws.world.add_item("c1", "partner.txt", parent="ext-root", owner=PARTNER, perms=[reader(BOB)])
    await ws.sync()
    assert "c1" in ws.records.records

    ws.world.delete("c1")
    await ws.sync()

    assert "c1" not in ws.records.records, "the owner can't be asked, and no synced user can open it"


async def test_a_file_owned_outside_the_workspace_stays_while_another_synced_user_can_open_it(ws: Workspace) -> None:
    ws.world.files["ext-root"] = FileState(
        {"id": "ext-root", "name": "My Drive", "mimeType": FOLDER, "owners": [{"emailAddress": PARTNER}], "parents": []}
    )
    ws.world.add_item("c1", "partner.txt", parent="ext-root", owner=PARTNER, perms=[reader(BOB), reader(ALICE)])
    await ws.sync()

    ws.world.unshare("c1", BOB)
    await ws.sync()

    assert "c1" in ws.records.records
    assert BOB not in ws.records.perm_emails("c1")
    assert ALICE in ws.records.perm_emails("c1")


async def test_a_file_whose_owner_is_not_synced_goes_when_its_last_synced_reader_loses_access(ws: Workspace) -> None:
    ws.records.active_users = [u for u in ws.records.active_users if u.email == ALICE]
    ws.world.folder("bob-dir", "Shared by Bob", parent="root-bob", owner=BOB, perms=[reader(ALICE)])
    ws.world.add_item("bob-f1", "brief.txt", parent="bob-dir", owner=BOB)
    await ws.sync()
    assert {"bob-dir", "bob-f1"} <= set(ws.records.records)

    ws.world.unshare("bob-dir", ALICE)
    await ws.sync()

    assert "bob-dir" not in ws.records.records, "Bob still has it, but nobody PipesHub syncs can open it"
    assert "bob-f1" not in ws.records.records


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


async def test_files_already_in_the_trash_are_not_synced(ws: Workspace) -> None:
    ws.world.add_item("a1", "keep.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("a2", "binned.txt", parent="root-alice", owner=ALICE)
    ws.world.trash("a2")

    await ws.sync()

    assert ws.names() == {"keep.txt"}


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


async def test_a_database_failure_on_a_change_keeps_the_users_checkpoint(ws: Workspace) -> None:
    ws.world.add_item("a1", "draft.txt", parent="root-alice", owner=ALICE)
    await ws.sync()
    checkpoint = ws.user_checkpoint(ALICE)
    ws.world.rename("a1", "final.txt")
    ws.records.fail_writes_for.add("a1")

    await ws.sync()

    assert ws.user_checkpoint(ALICE) == checkpoint


async def test_an_empty_page_with_a_next_token_does_not_end_a_users_full_sync(ws: Workspace) -> None:
    for n in range(4):
        ws.world.add_item(f"a{n}", f"a{n}.txt", parent="root-alice", owner=ALICE)
    ws.world.empty_page_at = 1

    await ws.sync()

    assert {f"a{n}.txt" for n in range(4)} <= ws.names()


async def test_one_bad_item_does_not_abort_the_users_sync_but_holds_their_checkpoint(ws: Workspace) -> None:
    ws.world.add_item("a1", "good.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("a2", "bad.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("a3", "also-good.txt", parent="root-alice", owner=ALICE)
    ws.world.files["a2"].meta["modifiedTime"] = "garbage"

    await ws.sync()

    assert {"good.txt", "also-good.txt"} <= ws.names()
    assert "bad.txt" not in ws.names()
    assert ws.incomplete, "a run that skipped an item is not reported as complete"
    assert ws.user_checkpoint(ALICE) is None, "nothing else would bring the skipped item back"


async def test_an_item_that_keeps_failing_holds_the_checkpoint_only_for_a_few_runs(ws: Workspace) -> None:
    from app.connectors.sources.google.drive.utils.checkpoint_hold import MAX_HELD_RUNS

    ws.world.add_item("a1", "good.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("a2", "bad.txt", parent="root-alice", owner=ALICE)
    ws.world.files["a2"].meta["modifiedTime"] = "garbage"

    for _ in range(MAX_HELD_RUNS):
        await ws.sync()

    assert ws.user_checkpoint(ALICE) is not None, "a feed must not stop for good on one item"


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


async def test_a_selected_folders_parent_shared_later_is_linked_on_the_next_sync(ws: Workspace) -> None:
    ws.world.hide_unreadable_parents = True
    ws.records.active_users = [u for u in ws.records.active_users if u.email == ALICE]
    ws.world.folder("bob-parent", "Bob's projects", parent="root-bob", owner=BOB)
    ws.world.folder("pick", "Picked", parent="bob-parent", owner=BOB, perms=[reader(ALICE)])
    ws.world.add_item("deep", "deep.txt", parent="pick", owner=BOB)
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick"]})
    await ws.sync()
    assert ws.records.records["pick"].parent_external_record_id is None, "Drive hides a parent Alice cannot open"

    ws.world.share("bob-parent", reader(ALICE))
    await ws.sync()

    parent = ws.records.records.get("bob-parent")
    assert parent is not None and not parent.is_placeholder and parent.record_name == "Bob's projects"
    assert ws.records.records["pick"].parent_external_record_id == "bob-parent"


async def test_a_selected_shortcut_syncs_the_folder_it_points_to(ws: Workspace) -> None:
    ws.world.folder("pick", "Picked", parent="root-bob", owner=BOB)
    ws.world.add_item("deep", "deep.txt", parent="pick", owner=BOB)
    ws.world.add_item("elsewhere", "elsewhere.txt", parent="root-bob", owner=BOB)
    ws.world.add_shortcut("sc", "Link to Picked", parent="root-bob", owner=BOB, target_id="pick")
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["sc"]})

    await ws.sync()

    assert {"Picked", "deep.txt"} <= ws.names()
    assert "elsewhere.txt" not in ws.names()


async def test_a_shortcut_target_one_user_can_read_is_not_reused_for_another(ws: Workspace) -> None:
    # The filler pushes Alice's shortcut onto a second page, so her sync fetches the target.
    ws.world.add_item("target", "Target.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("filler", "filler.txt", parent="root-alice", owner=ALICE)
    ws.world.add_shortcut("sc-a", "Alice link", parent="root-alice", owner=ALICE, target_id="target")
    ws.world.add_shortcut("sc-b", "Bob link", parent="root-bob", owner=BOB, target_id="target")

    await ws.sync()

    assert "Target.txt" in ws.names()
    assert ws.records.perm_emails("target") == {ALICE}


async def test_a_folder_shortcut_into_a_shared_drive_the_user_is_not_in_lists_its_contents(ws: Workspace) -> None:
    ws.world.add_user("carol@example.com")
    ws.world.add_drive("sd-x", "Carol's team", {"carol@example.com": "organizer"})
    ws.world.folder("sd-x-dir", "Handbook", parent="sd-x", perms=[reader(BOB)])
    ws.world.add_item("sd-x-page", "chapter-1.txt", parent="sd-x-dir")
    ws.world.add_shortcut("sc", "Link", parent="root-bob", owner=BOB, target_id="sd-x-dir")

    await ws.sync()

    assert {"Handbook", "chapter-1.txt"} <= ws.names()
    assert reaches(ws, BOB, "sd-x-page")
    assert ws.user_checkpoint(BOB) is not None


async def test_an_incomplete_listing_of_a_folder_entering_scope_keeps_the_users_checkpoint(ws: Workspace) -> None:
    ws.world.folder("pick", "Picked", parent="root-alice", owner=ALICE)
    ws.world.folder("outside", "Outside", parent="root-alice", owner=ALICE)
    ws.world.add_item("inner", "inner.txt", parent="outside", owner=ALICE)
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick"]})
    await ws.sync()
    before = ws.user_checkpoint(ALICE)

    ws.world.move("outside", "pick")
    ws.world.incomplete_search_when = lambda r: (
        r.query.get("q", "").startswith("trashed=false") and "'outside' in parents" in r.query.get("q", "")
    )
    await ws.sync()
    assert ws.user_checkpoint(ALICE) == before

    ws.world.incomplete_search_when = None
    await ws.sync()
    assert "inner.txt" in ws.names()


async def test_an_incomplete_subfolder_expansion_fails_the_run_without_a_checkpoint(ws: Workspace) -> None:
    ws.world.folder("pick", "Picked", parent="root-alice", owner=ALICE)
    ws.world.folder("pick-sub", "Sub", parent="pick", owner=ALICE)
    ws.world.add_item("deep", "deep.txt", parent="pick-sub", owner=ALICE)
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick"]})
    ws.world.incomplete_search_when = lambda r: (
        r.query.get("q", "").startswith("mimeType=") and "'pick' in parents" in r.query.get("q", "")
    )

    with pytest.raises(GoogleDriveError):
        await ws.sync()
    assert ws.user_checkpoint(ALICE) is None

    ws.world.incomplete_search_when = None
    await ws.sync()
    assert "deep.txt" in ws.names()


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


# --- narrowing the sync filters -----------------------------------------------


def narrow(ws: Workspace, **sync_values: object) -> None:
    """Save new sync filters the way the app does: saving clears every sync point."""
    ws.filters(**sync_values)
    ws.sync_points.sync_points.clear()


def selected_tree(ws: Workspace) -> None:
    ws.world.folder("top", "Top", parent="root-alice", owner=ALICE)
    ws.world.folder("main", "Main", parent="top", owner=ALICE)
    ws.world.add_item("keep", "keep.txt", parent="main", owner=ALICE)
    ws.world.folder("other", "Other", parent="top", owner=ALICE)
    ws.world.folder("other-sub", "Other sub", parent="other", owner=ALICE)
    ws.world.add_item("drop", "drop.txt", parent="other-sub", owner=ALICE)
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["top"]})



async def test_a_selected_folder_id_no_user_can_find_is_reported_to_the_admins(ws: Workspace) -> None:
    ws.world.folder("pick", "Picked", parent="root-bob", owner=BOB)
    ws.world.add_item("deep", "deep.txt", parent="pick", owner=BOB)
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick", "no-such-folder"]})
    notices: list[dict[str, Any]] = []

    async def notify(**kwargs: object) -> None:
        notices.append(kwargs)

    (await ws.connector_()).notify = notify
    await ws.sync()

    assert "deep.txt" in ws.names()
    assert [n["title"].split(": ", 1)[1] for n in notices] == ["some selected folders cannot be opened"]
    assert notices[0]["message"].startswith("1 folder in this connector's folder filter cannot be found")


async def test_selected_folders_no_user_could_be_asked_about_are_not_reported_missing(ws: Workspace) -> None:
    ws.world.folder("pick", "Picked", parent="root-bob", owner=BOB)
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick", "no-such-folder"]})
    notices: list[dict[str, Any]] = []

    async def notify(**kwargs: object) -> None:
        notices.append(kwargs)

    async def cannot_impersonate(user: object) -> None:
        raise RuntimeError("impersonation failed")

    connector = await ws.connector_()
    connector.notify = notify
    connector._build_user_drive_data_source = cannot_impersonate
    with contextlib.suppress(Exception):
        await ws.sync()

    assert not [n for n in notices if "selected folders" in n["title"]]


async def test_a_selected_folder_left_out_after_unrecognised_403s_is_reported(ws: Workspace) -> None:
    two_selected_folders(ws)
    ws.http.fail("GET", "/drive/v3/files/pick", 403, "someReasonDriveAddsLater")
    for _ in range(4):
        with pytest.raises(HttpError):
            await ws.sync()
    notices: list[dict[str, Any]] = []

    async def notify(**kwargs: object) -> None:
        notices.append(kwargs)

    (await ws.connector_()).notify = notify
    await ws.sync()

    assert [n["title"].split(": ", 1)[1] for n in notices] == ["some selected folders cannot be opened"]
    assert notices[0]["message"].startswith("1 folder in")

async def test_narrowing_the_folder_filter_removes_what_it_leaves_out(ws: Workspace) -> None:
    selected_tree(ws)
    await ws.sync()
    assert {"Top", "Main", "keep.txt", "Other", "Other sub", "drop.txt"} <= ws.names()
    drop_id = ws.records.records["drop"].id

    narrow(ws, folder_ids={"operator": "in", "type": "list", "value": ["main"]})
    await ws.sync()

    assert {"Main", "keep.txt"} <= ws.names()
    assert "Top" in ws.names(), "the folder above a selected one is kept, so the tree still leads to it"
    assert not {"Other", "Other sub", "drop.txt"} & ws.names()
    assert drop_id in ws.records.deleted


async def test_narrowing_the_extension_filter_removes_the_excluded_files(ws: Workspace) -> None:
    ws.world.add_item("t1", "notes.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("p1", "report.pdf", parent="root-alice", owner=ALICE)
    await ws.sync()

    narrow(ws, file_extensions={"operator": "in", "type": "multiselect", "value": ["txt"]})
    await ws.sync()

    assert ws.names() == {"notes.txt"}


async def test_narrowing_the_filters_removes_a_trashed_file_they_now_leave_out(ws: Workspace) -> None:
    ws.world.add_item("t1", "notes.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("p1", "report.pdf", parent="root-alice", owner=ALICE)
    await ws.sync()
    ws.records.records["p1"].is_deleted = True
    trashed_id = ws.records.records["p1"].id

    narrow(ws, file_extensions={"operator": "in", "type": "multiselect", "value": ["txt"]})
    await ws.sync()

    assert trashed_id in ws.records.deleted, "the filter cleanup must see trashed records, or this one stays forever"
    assert ws.names() == {"notes.txt"}


async def test_a_file_whose_name_has_no_extension_is_checked_by_its_stored_one(ws: Workspace) -> None:
    ws.world.add_item("q1", "Quarterly Report", parent="root-alice", owner=ALICE)
    ws.world.files["q1"].meta["fileExtension"] = "pdf"
    ws.world.add_item("t1", "notes.txt", parent="root-alice", owner=ALICE)
    await ws.sync()

    narrow(ws, file_extensions={"operator": "in", "type": "multiselect", "value": ["pdf"]})
    await ws.sync()

    assert ws.names() == {"Quarterly Report"}


def deep_selected_folder(ws: Workspace, top_parent: str, **owner: str) -> None:
    ws.world.folder("new", "New folder", parent=top_parent, **owner)
    ws.world.folder("nested", "nested folder 2", parent="new", **owner)
    ws.world.folder("pick", "test", parent="nested", **owner)
    ws.world.folder("sub-1", "subfolder 1", parent="pick", **owner)
    ws.world.add_item("deep", "deep.txt", parent="sub-1", **owner)
    ws.world.add_item("mine", "mine.txt", parent="root-alice", owner=ALICE)
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["pick"]})


async def assert_full_resync_keeps_the_path_to_the_selected_folder(ws: Workspace, group_id: str) -> None:
    await ws.sync()
    assert {"New folder", "nested folder 2", "test"} <= ws.names()

    ws.sync_points.sync_points.clear()
    await ws.sync()

    assert {"New folder", "nested folder 2", "test", "subfolder 1", "deep.txt"} <= ws.names()
    assert ws.records.deleted == []
    assert ws.records.records["pick"].parent_external_record_id == "nested"
    assert ws.records.records["nested"].parent_external_record_id == "new"
    assert {ws.records.records[i].external_record_group_id for i in ("new", "nested")} == {group_id}


async def test_a_full_resync_keeps_the_folders_above_a_selected_my_drive_folder(ws: Workspace) -> None:
    deep_selected_folder(ws, "root-alice", owner=ALICE)

    await assert_full_resync_keeps_the_path_to_the_selected_folder(ws, "root-alice")


async def test_a_full_resync_keeps_the_folders_above_a_selected_shared_drive_folder(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Engineering", {ALICE: "organizer"})
    deep_selected_folder(ws, "sd-1")

    await assert_full_resync_keeps_the_path_to_the_selected_folder(ws, "sd-1")


async def test_filters_that_did_not_change_remove_nothing_on_later_syncs(ws: Workspace) -> None:
    selected_tree(ws)
    await ws.sync()
    await ws.sync()

    assert {"keep.txt", "drop.txt"} <= ws.names()
    assert ws.records.deleted == []


async def test_a_selected_folder_nobody_can_list_holds_back_the_removal(ws: Workspace) -> None:
    selected_tree(ws)
    await ws.sync()

    ws.world.files["main"].meta["capabilities"]["canListChildren"] = False
    narrow(ws, folder_ids={"operator": "in", "type": "list", "value": ["main"]})
    await ws.sync()

    assert {"keep.txt", "drop.txt"} <= ws.names()
    assert ws.records.deleted == []

    ws.world.files["main"].meta["capabilities"]["canListChildren"] = True
    await ws.sync()

    assert "keep.txt" in ws.names()
    assert "drop.txt" not in ws.names()


async def test_a_selected_folder_nobody_can_list_keeps_its_subtree_out_of_stale_cleanup(ws: Workspace) -> None:
    # Enough files are seen that the unlisted subtree stays under the half-corpus limit.
    ws.world.folder("main", "Main", parent="root-alice", owner=ALICE)
    for n in range(4):
        ws.world.add_item(f"keep-{n}", f"keep-{n}.txt", parent="main", owner=ALICE)
    ws.world.folder("main-sub", "Main sub", parent="main", owner=ALICE)
    ws.world.add_item("deep", "deep.txt", parent="main-sub", owner=ALICE)
    ws.filters(folder_ids={"operator": "in", "type": "list", "value": ["main"]})
    await ws.sync()
    assert "deep.txt" in ws.names()

    ws.world.files["main"].meta["capabilities"]["canListChildren"] = False
    narrow(ws, folder_ids={"operator": "in", "type": "list", "value": ["main"]})
    await ws.sync()

    assert "deep.txt" in ws.names()
    assert ws.records.deleted == []


async def test_a_failed_record_listing_removes_nothing_and_is_retried(ws: Workspace) -> None:
    selected_tree(ws)
    await ws.sync()

    narrow(ws, folder_ids={"operator": "in", "type": "list", "value": ["main"]})
    ws.records.fail_record_listing = True
    await ws.sync()

    assert "drop.txt" in ws.names()
    assert ws.records.deleted == []

    ws.records.fail_record_listing = False
    await ws.sync()

    assert "drop.txt" not in ws.names()
    assert "keep.txt" in ws.names()


async def test_a_file_trashed_before_the_filters_were_saved_is_removed_by_the_full_sync(ws: Workspace) -> None:
    ws.world.add_item("a1", "binned.txt", parent="root-alice", owner=ALICE)
    ws.world.add_item("a2", "keep.txt", parent="root-alice", owner=ALICE)
    await ws.sync()

    ws.world.trash("a1")
    narrow(ws, file_extensions={"operator": "in", "type": "multiselect", "value": ["txt"]})
    await ws.sync()

    assert ws.names() == {"keep.txt"}


async def test_a_removal_that_fails_to_save_is_retried_next_sync(ws: Workspace) -> None:
    selected_tree(ws)
    await ws.sync()

    narrow(ws, folder_ids={"operator": "in", "type": "list", "value": ["main"]})
    ws.records.fail_writes_for.add("drop")
    await ws.sync()

    assert "drop.txt" in ws.names()

    ws.records.fail_writes_for.discard("drop")
    await ws.sync()

    assert "drop.txt" not in ws.names()
    assert {"Main", "keep.txt"} <= ws.names()


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


async def test_reindex_of_an_unchanged_indexed_file_asks_for_a_reindex(ws: Workspace) -> None:
    ws.world.folder("dir", "Docs", parent="root-alice", owner=ALICE)
    ws.world.add_item("a1", "same.txt", parent="dir", owner=ALICE)
    await ws.sync()
    connector = await ws.connector_()

    await connector.reindex_records([ws.records.records["a1"]])

    assert [r.external_record_id for r in ws.records.reindexed] == ["a1"]


# --- shared drive restrictions ------------------------------------------------

CAROL = "carol@example.com"
MEMBERS_ONLY = {"driveMembersOnly": True}
DOMAIN_ONLY = {"domainUsersOnly": True}


def restricted_drive(ws: Workspace, restrictions: Optional[dict[str, bool]] = None) -> None:
    """A drive whose members are Alice and the eng group (Bob); one item is shared with Bob (a member through the
    group), a person of the domain outside the drive, an outsider, the member group and a group that is not."""
    ws.world.add_user(CAROL)
    ws.world.add_group("eng@example.com", [BOB])
    ws.world.add_group("other@example.com", [CAROL])
    ws.world.add_drive("sd-1", "Board", {ALICE: "organizer", "eng@example.com": "reader"}, restrictions)
    ws.world.add_item("f1", "plan.txt", parent="sd-1", perms=[
        {"type": "user", "role": "writer", "emailAddress": BOB}, reader(CAROL), reader(PARTNER),
        {"type": "group", "role": "reader", "emailAddress": "eng@example.com"},
        {"type": "group", "role": "reader", "emailAddress": "other@example.com"},
    ])


def f1_people(ws: Workspace) -> set[str]:
    return {who for who, _, _ in ws.grants("f1")}


EVERYONE = {BOB, CAROL, PARTNER, "eng@example.com", "other@example.com"}
MEMBERS = {BOB, "eng@example.com"}


async def test_a_drive_without_restrictions_keeps_every_share(ws: Workspace) -> None:
    restricted_drive(ws)
    await ws.sync()
    assert f1_people(ws) == EVERYONE


async def test_members_only_keeps_the_members_and_drops_every_other_share(ws: Workspace) -> None:
    restricted_drive(ws, MEMBERS_ONLY)
    await ws.sync()
    assert f1_people(ws) == MEMBERS
    assert ws.incomplete is False


async def test_members_only_drops_a_non_member_who_is_shared_with_on_a_shared_with_me_link(ws: Workspace) -> None:
    restricted_drive(ws, MEMBERS_ONLY)
    await ws.sync()
    assert "0S:carol@example.com" not in ws.records.records["f1"].shared_with_me_record_group_ids
    assert "0S:bob@example.com" in ws.records.records["f1"].shared_with_me_record_group_ids


async def test_domain_only_keeps_people_of_the_domain_and_drops_the_outsider(ws: Workspace) -> None:
    restricted_drive(ws, DOMAIN_ONLY)
    await ws.sync()
    assert f1_people(ws) == EVERYONE - {PARTNER}


async def test_domain_only_drops_an_outside_member_of_the_drive_itself(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Board", {ALICE: "organizer", PARTNER: "reader"}, DOMAIN_ONLY)
    await ws.sync()
    assert {p.email for p in ws.records.record_group_permissions["sd-1"]} == {ALICE}


async def test_both_restrictions_together_leave_the_members_of_the_domain(ws: Workspace) -> None:
    restricted_drive(ws, {**MEMBERS_ONLY, **DOMAIN_ONLY})
    await ws.sync()
    assert f1_people(ws) == MEMBERS


async def test_a_restriction_turned_on_between_syncs_cuts_shares_nobody_changed_and_turning_it_off_restores_them(
    ws: Workspace,
) -> None:
    restricted_drive(ws)
    await ws.sync()
    assert f1_people(ws) == EVERYONE

    ws.world.drives["sd-1"]["restrictions"] = {**MEMBERS_ONLY, **DOMAIN_ONLY}
    await ws.sync()
    assert f1_people(ws) == MEMBERS

    ws.world.drives["sd-1"]["restrictions"] = {}
    await ws.sync()
    assert f1_people(ws) == EVERYONE


async def test_a_restriction_that_does_not_change_does_not_relist_the_drive(ws: Workspace) -> None:
    restricted_drive(ws, MEMBERS_ONLY)
    await ws.sync()
    await ws.sync()
    assert f1_people(ws) == MEMBERS
    assert len([k for k, w in ws.sync_points.writes if "shared_drive_restrictions_sd-1" in k and "driveMembersOnly" in w]) == 1


@pytest.mark.parametrize(("truth", "people"), [(MEMBERS_ONLY, MEMBERS), (None, EVERYONE)])
async def test_restrictions_left_out_of_the_listing_are_asked_for_directly(
    ws: Workspace, truth: Optional[dict[str, bool]], people: set[str]
) -> None:
    restricted_drive(ws, truth)
    ws.world.restrictions_unlisted = True
    await ws.sync()
    assert f1_people(ws) == people
    assert ws.incomplete is False


async def test_restrictions_that_cannot_be_read_fail_closed_and_keep_the_stored_grants(ws: Workspace) -> None:
    restricted_drive(ws)
    await ws.sync()
    assert f1_people(ws) == EVERYONE

    ws.world.drives["sd-1"]["restrictions"] = MEMBERS_ONLY
    ws.world.restrictions_unlisted = True
    ws.world.restrictions_unreadable = True
    ws.world.share("f1", reader("late@example.com"))
    await ws.sync()

    assert ws.incomplete is True
    assert f1_people(ws) == EVERYONE  # not widened (no late share), not cut: the item is read again next run

    ws.world.restrictions_unlisted = False
    ws.world.restrictions_unreadable = False
    await ws.sync()
    assert ws.incomplete is False
    assert f1_people(ws) == MEMBERS


def all_grants(ws: Workspace) -> dict[str, set[tuple[Optional[str], str, str]]]:
    groups = {
        f"group:{k}": {(p.email or p.external_id, p.entity_type.name, p.type.name) for p in v}
        for k, v in ws.records.record_group_permissions.items()
    }
    return {**{k: ws.grants(k) for k in ws.records.permissions}, **groups}


def restrictions_left_out(ws: Workspace, left_out: bool) -> None:
    ws.world.restrictions_unlisted = ws.world.restrictions_omitted = left_out


@pytest.mark.parametrize(("truth", "healed"), [(None, EVERYONE), (MEMBERS_ONLY, MEMBERS)])
async def test_a_new_drive_answering_without_restrictions_is_read_as_restricted_until_an_answer_has_them(
    ws: Workspace, truth: Optional[dict[str, bool]], healed: set[str]
) -> None:
    restricted_drive(ws, truth)
    restrictions_left_out(ws, True)
    await ws.sync()

    assert ws.incomplete is True
    assert f1_people(ws) == MEMBERS  # no share beyond what members-only and domain-only both allow
    restriction_writes = [w for k, w in ws.sync_points.writes if "shared_drive_restrictions_sd-1" in k]
    assert restriction_writes and not [w for w in restriction_writes if "driveMembersOnly" in w or "domainUsersOnly" in w]

    restrictions_left_out(ws, False)
    await ws.sync()
    assert ws.incomplete is False
    assert f1_people(ws) == healed


async def test_an_unrestricted_drive_read_as_restricted_for_one_run_gets_every_grant_back(ws: Workspace) -> None:
    restricted_drive(ws)
    await ws.sync()
    before = all_grants(ws)

    restrictions_left_out(ws, True)
    ws.world.share("f1", reader("late@example.com"))
    await ws.sync()
    assert ws.incomplete is True
    assert f1_people(ws) == MEMBERS  # read again under the assumed restrictions

    restrictions_left_out(ws, False)
    await ws.sync()
    assert ws.incomplete is False
    assert all_grants(ws) == {**before, "f1": before["f1"] | {("late@example.com", "USER", "READ")}}


async def test_a_file_shared_out_of_a_drive_with_no_synced_member_gets_its_grant_back(ws: Workspace) -> None:
    # Only Bob's own listing (files shared with him) reaches this file, and his checkpoint moves on after the run.
    ws.world.add_drive("sd-2", "Outside", {PARTNER: "organizer"})
    ws.world.add_item("g1", "memo.txt", parent="sd-2", perms=[BOB_WRITES])
    restrictions_left_out(ws, True)
    await ws.sync()
    assert BOB not in {who for who, _, _ in ws.grants("g1")}

    restrictions_left_out(ws, False)
    await ws.sync()
    assert BOB in {who for who, _, _ in ws.grants("g1")}


async def stored_as_unrestricted(ws: Workspace) -> None:
    """sd-1 (members Alice and eng) and sd-2 (no synced member; g1 shared with Bob), both with unrestricted flags
    stored: each was members-only, then lifted."""
    restricted_drive(ws, MEMBERS_ONLY)
    ws.world.add_drive("sd-2", "Outside", {PARTNER: "organizer"}, MEMBERS_ONLY)
    ws.world.add_item("g1", "memo.txt", parent="sd-2", perms=[BOB_WRITES])
    await ws.sync()
    for drive in ("sd-1", "sd-2"):
        ws.world.drives[drive]["restrictions"] = {}
    await ws.sync()
    assert f1_people(ws) == EVERYONE and BOB in g1_people(ws)
    assert ws.sync_points.value("shared_drive_restrictions_sd-1")["driveMembersOnly"] is False


async def test_stored_unrestricted_flags_are_not_trusted_when_a_tightened_drive_answers_without_them(
    ws: Workspace,
) -> None:
    await stored_as_unrestricted(ws)
    for drive in ("sd-1", "sd-2"):
        ws.world.drives[drive]["restrictions"] = MEMBERS_ONLY
    restrictions_left_out(ws, True)
    await ws.sync()

    # Neither file changed, so no feed reports them: the stored, looser flags must not keep their outsiders.
    assert ws.incomplete is True
    assert f1_people(ws) == MEMBERS and BOB not in g1_people(ws)
    restrictions_left_out(ws, False)
    await ws.sync()
    assert f1_people(ws) == MEMBERS and BOB not in g1_people(ws)


async def test_a_drive_stored_as_unrestricted_answering_without_restrictions_heals_fully(ws: Workspace) -> None:
    await stored_as_unrestricted(ws)
    before = all_grants(ws)

    restrictions_left_out(ws, True)
    ws.world.share("f1", reader("late@example.com"))
    ws.world.share("g1", reader("erin@partner.org"))  # g1 is read again, through Bob's feed only
    await ws.sync()
    assert ws.incomplete is True
    assert f1_people(ws) == MEMBERS and BOB not in g1_people(ws)  # one run under the assumed limits
    assert ws.sync_points.value("shared_drive_restrictions_sd-1")["driveMembersOnly"] is False  # left as stored

    await ws.sync()  # still left out: the mark is kept and the drive is not read again
    assert ws.sync_points.value("shared_drive_restrictions_sd-1")["assumed"] is True

    restrictions_left_out(ws, False)
    await ws.sync()
    assert ws.incomplete is False
    assert ws.sync_points.value("shared_drive_restrictions_sd-1")["assumed"] is False
    assert all_grants(ws) == {
        **before,
        "f1": before["f1"] | {("late@example.com", "USER", "READ")},
        "g1": before["g1"] | {("erin@partner.org", "USER", "READ")},
    }


# A writer reads the item's sharing; a reader is answered an empty list and synced with their own grant only.
BOB_WRITES = {"type": "user", "role": "writer", "emailAddress": BOB}


def g1_people(ws: Workspace) -> set[str]:
    return {who for who, _, _ in ws.grants("g1")}


@pytest.mark.parametrize(("limit", "outsider"), [(MEMBERS_ONLY, BOB), (DOMAIN_ONLY, "erin@partner.org")])
async def test_a_file_shared_out_of_a_drive_with_no_synced_member_comes_back_when_its_limit_is_lifted(
    ws: Workspace, limit: dict[str, bool], outsider: str
) -> None:
    # Only Bob's own listing (files shared with him) reaches this file; lifting the limit changes nothing in his feed.
    ws.world.add_drive("sd-2", "Outside", {PARTNER: "organizer"}, limit)
    ws.world.add_item("g1", "memo.txt", parent="sd-2", perms=[BOB_WRITES, reader("erin@partner.org")])
    await ws.sync()
    assert outsider not in g1_people(ws)

    ws.world.drives["sd-2"]["restrictions"] = {}
    await ws.sync()
    assert ws.incomplete is False
    assert {BOB, "erin@partner.org"} <= g1_people(ws)


async def test_a_file_shared_out_of_a_drive_with_no_synced_member_is_cut_when_a_limit_is_set(ws: Workspace) -> None:
    ws.world.add_drive("sd-2", "Outside", {PARTNER: "organizer"})
    ws.world.add_item("g1", "memo.txt", parent="sd-2", perms=[BOB_WRITES])
    await ws.sync()
    assert BOB in g1_people(ws)

    ws.world.drives["sd-2"]["restrictions"] = MEMBERS_ONLY
    await ws.sync()
    assert BOB not in g1_people(ws)


class Crash(BaseException):
    """The process stops here: no handler in the connector catches it."""


async def restart(ws: Workspace) -> None:
    """A new connector object over the same graph and sync points: nothing in memory survives."""
    old, ws.connector = ws.connector, None
    with contextlib.suppress(Exception):
        await old.cleanup()


def crash_on_write(ws: Workspace, key_suffix: str, data: dict[str, Any]) -> None:
    """The next write of `data` to the sync point ending in `key_suffix` never happens: the process stops first."""
    real = ws.sync_points.update_sync_point

    async def update(key: str, written: dict[str, Any]) -> None:
        if key.endswith(key_suffix) and data.items() <= written.items():
            ws.sync_points.update_sync_point = real
            raise Crash
        await real(key, written)

    ws.sync_points.update_sync_point = update


def crash_on_reset(ws: Workspace, key_suffix: str) -> None:
    """The next reset of the token ending in `key_suffix` never happens: the process stops first."""
    real = ws.sync_points.delete_sync_point

    async def delete(key: str) -> None:
        if key.endswith(key_suffix):
            ws.sync_points.delete_sync_point = real
            raise Crash
        await real(key)

    ws.sync_points.delete_sync_point = delete


async def crash_in_bobs_shared_read(ws: Workspace) -> None:
    """Bob's shared-with-me read stops after it has written its items, before it finishes."""
    connector = await ws.connector_()
    real = connector._process_shared_with_me_items

    async def process(**kwargs: object) -> tuple[list, int, int]:
        out = await real(**kwargs)
        if getattr(kwargs["user"], "email", None) == BOB:
            raise Crash
        return out

    connector._process_shared_with_me_items = process


def bob_checkpoint(ws: Workspace) -> dict[str, Any]:
    return ws.sync_points.value(f"/users/{ws.world.users[BOB].user_id}") or {}


async def tightened_and_left_out(ws: Workspace) -> dict[str, set[tuple[Optional[str], str, str]]]:
    """Both drives stored as unrestricted, then made members-only at the source, which answers without restrictions.
    Returns the grants before, which the drives get back once they are unrestricted again."""
    await stored_as_unrestricted(ws)
    before = all_grants(ws)
    for drive in ("sd-1", "sd-2"):
        ws.world.drives[drive]["restrictions"] = MEMBERS_ONLY
    restrictions_left_out(ws, True)
    return before


async def lifted_with_the_object(ws: Workspace, before: dict[str, set[tuple[Optional[str], str, str]]]) -> None:
    for drive in ("sd-1", "sd-2"):
        ws.world.drives[drive]["restrictions"] = {}
    restrictions_left_out(ws, False)
    await ws.sync()
    assert ws.incomplete is False
    assert ws.sync_points.value("shared_drive_restrictions_sd-1")["assumed"] is False
    assert bob_checkpoint(ws).get("reseed_shared_with_me") is not True
    assert all_grants(ws) == before


@pytest.mark.parametrize("stop", ["at the first token reset", "after the resets, before the mark"])
async def test_a_restart_between_the_token_reset_and_the_mark_reads_the_drive_again_under_the_assumed_limits(
    ws: Workspace, stop: str
) -> None:
    before = await tightened_and_left_out(ws)
    if stop == "at the first token reset":
        crash_on_reset(ws, "_sd-1")
    else:
        crash_on_write(ws, "shared_drive_restrictions_sd-1", {"assumed": True})
    with pytest.raises(Crash):
        await ws.sync()
    assert not ws.sync_points.value("shared_drive_restrictions_sd-1").get("assumed")

    await restart(ws)
    await ws.sync()  # still left out: no mark was stored, so this run resets and reads again
    assert ws.incomplete is True
    assert f1_people(ws) == MEMBERS and BOB not in g1_people(ws)
    assert ws.sync_points.value("shared_drive_restrictions_sd-1")["assumed"] is True

    await restart(ws)
    await lifted_with_the_object(ws, before)


async def test_a_restart_after_the_mark_finishes_the_interrupted_shared_read_under_the_assumed_limits(
    ws: Workspace,
) -> None:
    before = await tightened_and_left_out(ws)
    await crash_in_bobs_shared_read(ws)
    await ws.sync()  # Bob's sync stops partway; the run ends incomplete
    assert ws.sync_points.value("shared_drive_restrictions_sd-1")["assumed"] is True
    assert bob_checkpoint(ws)["reseed_shared_with_me"] is True  # kept: the read did not finish

    await restart(ws)
    await ws.sync()  # still left out: the mark is stored, so tokens stay, and Bob's read is done again
    assert ws.incomplete is True
    assert f1_people(ws) == MEMBERS and BOB not in g1_people(ws)
    assert bob_checkpoint(ws)["reseed_shared_with_me"] is False

    await restart(ws)
    await lifted_with_the_object(ws, before)


async def test_a_shared_read_interrupted_after_a_limit_is_lifted_is_finished_by_a_new_process(ws: Workspace) -> None:
    ws.world.add_drive("sd-2", "Outside", {PARTNER: "organizer"}, MEMBERS_ONLY)
    ws.world.add_item("g1", "memo.txt", parent="sd-2", perms=[BOB_WRITES])
    await ws.sync()
    assert BOB not in g1_people(ws)

    ws.world.drives["sd-2"]["restrictions"] = {}
    await crash_in_bobs_shared_read(ws)
    await ws.sync()
    assert bob_checkpoint(ws)["reseed_shared_with_me"] is True  # cleared only after a complete read

    await restart(ws)
    await ws.sync()
    assert ws.incomplete is False
    assert BOB in g1_people(ws)
    assert bob_checkpoint(ws)["reseed_shared_with_me"] is False


async def test_a_new_process_heals_a_drive_marked_assumed_on_the_first_answer_with_restrictions(ws: Workspace) -> None:
    before = await tightened_and_left_out(ws)
    await ws.sync()
    assert ws.sync_points.value("shared_drive_restrictions_sd-1")["assumed"] is True
    assert f1_people(ws) == MEMBERS and BOB not in g1_people(ws)

    await restart(ws)
    await lifted_with_the_object(ws, before)


def bob_as(role: str) -> dict[str, str]:
    return {"type": "user", "role": role, "emailAddress": BOB}


# A reader or commenter of a shared drive item is answered an empty permission list: their grant is their own.
@pytest.mark.parametrize("role", ["reader", "commenter"])
async def test_a_reader_of_a_file_shared_out_of_a_drive_is_cut_on_the_run_a_limit_is_set(ws: Workspace, role: str) -> None:
    ws.world.add_drive("sd-2", "Outside", {PARTNER: "organizer"})
    ws.world.add_item("g1", "memo.txt", parent="sd-2", perms=[bob_as(role)])
    await ws.sync()
    assert BOB in g1_people(ws)

    ws.world.drives["sd-2"]["restrictions"] = MEMBERS_ONLY
    await ws.sync()
    assert BOB not in g1_people(ws)

    ws.world.drives["sd-2"]["restrictions"] = {}
    await ws.sync()
    assert BOB in g1_people(ws)


@pytest.mark.parametrize("role", ["reader", "commenter"])
async def test_a_reader_of_a_file_shared_out_of_a_drive_is_cut_while_its_restrictions_are_left_out(
    ws: Workspace, role: str
) -> None:
    ws.world.add_drive("sd-2", "Outside", {PARTNER: "organizer"})
    ws.world.add_item("g1", "memo.txt", parent="sd-2", perms=[bob_as(role)])
    await ws.sync()
    assert BOB in g1_people(ws)

    restrictions_left_out(ws, True)
    await ws.sync()
    assert ws.incomplete is True
    assert BOB not in g1_people(ws)

    restrictions_left_out(ws, False)
    await ws.sync()
    assert BOB in g1_people(ws)


@pytest.mark.parametrize("role", ["reader", "commenter"])
async def test_a_reader_of_a_file_shared_out_of_a_drive_gets_it_back_when_its_limit_is_lifted(
    ws: Workspace, role: str
) -> None:
    ws.world.add_drive("sd-2", "Outside", {PARTNER: "organizer"}, MEMBERS_ONLY)
    ws.world.add_item("g1", "memo.txt", parent="sd-2", perms=[bob_as(role)])
    await ws.sync()
    assert BOB not in g1_people(ws)

    ws.world.drives["sd-2"]["restrictions"] = {}
    await ws.sync()
    assert BOB in g1_people(ws)


def shared_listings(ws: Workspace) -> int:
    return len([r for r in ws.http.calls("GET", "/drive/v3/files") if "sharedWithMe" in r.query.get("q", "")])


async def test_a_file_that_fails_during_a_shared_read_is_read_again_by_id_until_it_succeeds(ws: Workspace) -> None:
    from app.connectors.sources.google.drive.utils.checkpoint_hold import MAX_HELD_RUNS

    ws.world.add_drive("sd-2", "Outside", {PARTNER: "organizer"}, MEMBERS_ONLY)
    ws.world.add_item("g1", "memo.txt", parent="sd-2", perms=[BOB_WRITES])
    ws.world.add_item("g2", "notes.txt", parent="sd-2", perms=[BOB_WRITES])
    await ws.sync()
    assert BOB not in g1_people(ws)

    # Lifting the limit changes Bob's grant on g1, but not g1: no feed reports it, and its read keeps failing.
    ws.world.drives["sd-2"]["restrictions"] = {}
    ws.http.fail("GET", "/drive/v3/files/g1/permissions", 500, "backendError")
    for _ in range(MAX_HELD_RUNS + 1):  # past the runs a failure holds the checkpoint for
        await ws.sync()
        assert bob_checkpoint(ws)["reseed_shared_with_me"] is True
    assert bob_checkpoint(ws)["failed_items"] == ["g1"]
    assert BOB in {who for who, _, _ in ws.grants("g2")} and BOB not in g1_people(ws)

    await restart(ws)
    ws.http.clear_faults()
    listings = shared_listings(ws)
    await ws.sync()
    assert BOB in g1_people(ws)
    assert bob_checkpoint(ws)["reseed_shared_with_me"] is False and bob_checkpoint(ws)["failed_items"] == []
    assert shared_listings(ws) == listings  # read again by id, not by listing everything shared with Bob


async def test_an_item_that_fails_while_a_drive_is_read_under_assumed_limits_is_read_again_until_it_succeeds(
    ws: Workspace,
) -> None:
    from app.connectors.sources.google.drive.utils.checkpoint_hold import MAX_HELD_RUNS

    await stored_as_unrestricted(ws)
    ws.world.drives["sd-1"]["restrictions"] = MEMBERS_ONLY
    restrictions_left_out(ws, True)
    ws.http.fail("GET", "/drive/v3/files/f1/permissions", 500, "backendError")
    for _ in range(MAX_HELD_RUNS + 1):
        await ws.sync()
    assert ws.incomplete is True

    await restart(ws)
    ws.http.clear_faults()
    await ws.sync()  # still left out: the drive is not listed again, f1 alone is read again by id
    assert f1_people(ws) == MEMBERS


async def g1_left_to_retry(ws: Workspace) -> None:
    """sd-2 (no synced member) holds g1, which Bob writes and whose read keeps failing, and g2, which Bob reads.
    After the runs a failure holds Bob's checkpoint for, g1 is left on it to read again by id."""
    from app.connectors.sources.google.drive.utils.checkpoint_hold import MAX_HELD_RUNS

    ws.world.add_drive("sd-2", "Outside", {PARTNER: "organizer"})
    ws.world.add_item("g1", "memo.txt", parent="sd-2", perms=[BOB_WRITES])
    ws.world.add_item("g2", "notes.txt", parent="sd-2", perms=[bob_as("reader")])
    ws.http.fail("GET", "/drive/v3/files/g1/permissions", 500, "backendError")
    for _ in range(MAX_HELD_RUNS):
        await ws.sync()
    assert bob_checkpoint(ws)["failed_items"] == ["g1"]


def g2_people(ws: Workspace) -> set[str]:
    return {who for who, _, _ in ws.grants("g2")}


async def test_a_retry_list_left_from_an_earlier_read_never_stands_in_for_a_full_shared_read(ws: Workspace) -> None:
    await g1_left_to_retry(ws)
    assert BOB in g2_people(ws)

    # g2 does not change; only the drive's limit does. Reading g1 alone again would leave Bob on g2.
    ws.world.drives["sd-2"]["restrictions"] = MEMBERS_ONLY
    await restart(ws)
    await ws.sync()
    assert BOB not in g2_people(ws)
    assert bob_checkpoint(ws)["reseed_shared_with_me"] is True  # g1 still fails

    ws.http.clear_faults()
    await restart(ws)
    await ws.sync()
    assert BOB not in g2_people(ws) and BOB not in g1_people(ws)
    assert bob_checkpoint(ws)["reseed_shared_with_me"] is False


async def test_a_drive_retry_list_never_stands_in_for_the_full_read_a_limit_change_asks_for(ws: Workspace) -> None:
    from app.connectors.sources.google.drive.utils.checkpoint_hold import MAX_HELD_RUNS

    restricted_drive(ws)
    ws.world.add_item("f2", "notes.txt", parent="sd-1", perms=[reader(PARTNER)])
    ws.http.fail("GET", "/drive/v3/files/f1/permissions", 500, "backendError")
    for _ in range(MAX_HELD_RUNS):
        await ws.sync()
    assert ws.sync_points.value(f"/drives/{ws.world.users[ALICE].user_id}_sd-1")["failed_items"] == ["f1"]
    assert PARTNER in {who for who, _, _ in ws.grants("f2")}

    ws.world.drives["sd-1"]["restrictions"] = MEMBERS_ONLY  # f2 does not change
    await restart(ws)
    await ws.sync()
    assert PARTNER not in {who for who, _, _ in ws.grants("f2")}


async def test_an_item_retried_by_id_that_fails_again_does_not_hold_the_users_feed(ws: Workspace) -> None:
    await g1_left_to_retry(ws)  # past the hold: the token moved on, g1 left to retry
    token = bob_checkpoint(ws)["pageToken"]

    ws.world.add_item("g3", "later.txt", parent="sd-2", perms=[BOB_WRITES])  # a change in Bob's feed
    await ws.sync()  # g1 fails once more, now as a retry
    assert bob_checkpoint(ws)["pageToken"] != token
    assert not bob_checkpoint(ws).get("heldFailedItemRuns")
    assert bob_checkpoint(ws)["failed_items"] == ["g1"]
    assert BOB in {who for who, _, _ in ws.grants("g3")}


async def test_an_item_retried_by_id_that_fails_again_does_not_hold_the_drives_feed(ws: Workspace) -> None:
    from app.connectors.sources.google.drive.utils.checkpoint_hold import MAX_HELD_RUNS

    restricted_drive(ws)
    ws.http.fail("GET", "/drive/v3/files/f1/permissions", 500, "backendError")
    for _ in range(MAX_HELD_RUNS):
        await ws.sync()
    key = f"/drives/{ws.world.users[ALICE].user_id}_sd-1"
    token = ws.sync_points.value(key)["pageToken"]

    ws.world.add_item("f3", "later.txt", parent="sd-1", perms=[reader(CAROL)])  # a change in the drive's feed
    await ws.sync()  # f1 fails once more, now as a retry
    assert ws.sync_points.value(key)["pageToken"] != token
    assert not ws.sync_points.value(key).get("heldFailedItemRuns")
    assert ws.sync_points.value(key)["failed_items"] == ["f1"]
    assert CAROL in {who for who, _, _ in ws.grants("f3")}


async def test_a_failed_item_trashed_before_it_could_be_read_again_is_not_written(ws: Workspace) -> None:
    await g1_left_to_retry(ws)
    assert "memo.txt" not in ws.names()  # never written: its read failed every time

    ws.world.trash("g1")
    ws.http.clear_faults()
    await ws.sync()
    assert "memo.txt" not in ws.names()
    assert bob_checkpoint(ws)["failed_items"] == []


async def test_a_failed_retry_is_logged_with_its_status_and_reason_but_no_id(
    ws: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    await g1_left_to_retry(ws)
    ws.http.fail("GET", "/drive/v3/files/g1", 403, "insufficientFilePermissions")
    caplog.clear()
    await ws.sync()
    retried = [r.getMessage() for r in caplog.records if "read again" in r.getMessage() and "403" in r.getMessage()]
    assert retried and "insufficientFilePermissions" in retried[0]
    assert "g1" not in retried[0] and BOB not in retried[0]


async def test_an_unreadable_answer_for_a_new_drive_shares_nothing(ws: Workspace) -> None:
    restricted_drive(ws)
    ws.world.restrictions_unlisted = ws.world.restrictions_unreadable = True
    await ws.sync()
    assert ws.incomplete is True
    assert f1_people(ws) <= MEMBERS

    ws.world.restrictions_unlisted = ws.world.restrictions_unreadable = False
    await ws.sync()
    assert f1_people(ws) == EVERYONE


async def test_the_organization_drive_listing_asks_for_the_restrictions(ws: Workspace) -> None:
    restricted_drive(ws, MEMBERS_ONLY)
    await ws.sync()
    admin_listings = [r for r in ws.http.calls("GET", "/drive/v3/drives") if r.query.get("useDomainAdminAccess") == "true"]
    assert admin_listings
    assert all("restrictions" in r.query["fields"] for r in admin_listings)


@pytest.mark.parametrize("status", [404, 403])
async def test_members_only_hides_what_an_unreadable_member_group_would_have_vouched_for(
    ws: Workspace, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    restricted_drive(ws, MEMBERS_ONLY)
    ws.world.unlistable_groups["eng@example.com"] = status

    await ws.sync()

    # Bob is a member only through the group the directory will not list: his direct share waits until it can be read.
    # The member group itself keeps its grant, and nobody who is not known to be a member is let in.
    assert f1_people(ws) == {"eng@example.com"}
    assert any("sd-1" in r.getMessage() and "eng@example.com" in r.getMessage() for r in caplog.records if r.levelname == "WARNING")


async def test_a_member_group_that_could_not_be_listed_vouches_again_once_it_can(ws: Workspace) -> None:
    restricted_drive(ws, MEMBERS_ONLY)
    ws.world.unlistable_groups["eng@example.com"] = 404
    await ws.sync()
    assert BOB not in f1_people(ws)

    del ws.world.unlistable_groups["eng@example.com"]
    await ws.sync()
    assert f1_people(ws) == MEMBERS


async def test_members_only_replaces_a_group_that_is_not_a_member_with_its_members_who_are(ws: Workspace) -> None:
    ws.world.add_user(CAROL)
    ws.world.add_group("partial@example.com", [BOB, CAROL])
    ws.world.add_drive("sd-1", "Board", {ALICE: "organizer", BOB: "writer"}, MEMBERS_ONLY)
    ws.world.add_item("f1", "plan.txt", parent="sd-1", perms=[
        {"type": "group", "role": "writer", "emailAddress": "partial@example.com"},
    ])

    await ws.sync()

    assert ws.grants("f1") == {(BOB, "USER", "WRITE")}


async def test_members_only_counts_the_people_of_a_member_group_who_are_not_synced_users(ws: Workspace) -> None:
    ws.world.add_group("eng@example.com", [BOB, PARTNER])
    ws.world.add_group("mixed@example.com", [BOB, PARTNER])
    ws.world.add_drive("sd-1", "Board", {ALICE: "organizer", "eng@example.com": "reader"}, MEMBERS_ONLY)
    ws.world.add_item("f1", "plan.txt", parent="sd-1", perms=[
        reader(PARTNER), {"type": "group", "role": "reader", "emailAddress": "mixed@example.com"},
    ])

    await ws.sync()

    # The list is read as Alice, the drive's Manager: Bob reads it as a member and through mixed@, and has no grant
    # of his own on it. Dana is a member only through the group and is not a synced user.
    assert f1_people(ws) == {PARTNER, "mixed@example.com"}


async def test_members_only_keeps_a_group_nested_in_a_member_group_whole(ws: Workspace) -> None:
    ws.world.add_user(CAROL)
    ws.world.add_group("inner@example.com", [BOB])
    ws.world.add_group("outer@example.com", ["inner@example.com"])
    ws.world.add_group("stranger@example.com", [CAROL])
    ws.world.add_drive("sd-1", "Board", {ALICE: "organizer", "outer@example.com": "writer"}, MEMBERS_ONLY)
    ws.world.add_item("f1", "plan.txt", parent="sd-1", perms=[
        {"type": "group", "role": "writer", "emailAddress": "inner@example.com"},
        {"type": "group", "role": "writer", "emailAddress": "stranger@example.com"},
    ])

    await ws.sync()

    # Everyone in the nested group is a member, so it stays whole; the stranger group has no member and goes.
    assert ws.grants("f1") == {("inner@example.com", "GROUP", "WRITE")}


async def test_a_group_that_is_not_a_member_and_cannot_be_listed_is_hidden_with_a_warning(
    ws: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    ws.world.add_group("partial@example.com", [BOB])
    ws.world.add_drive("sd-1", "Board", {ALICE: "organizer", BOB: "writer"}, MEMBERS_ONLY)
    ws.world.add_item("f1", "plan.txt", parent="sd-1", perms=[
        {"type": "group", "role": "writer", "emailAddress": "partial@example.com"},
    ])
    ws.world.unlistable_groups["partial@example.com"] = 404

    await ws.sync()

    assert ws.grants("f1") == set()
    assert any("sd-1" in r.getMessage() and "partial@example.com" in r.getMessage() for r in caplog.records if r.levelname == "WARNING")


def domain_drive(ws: Workspace, groups: dict[str, list[str]]) -> None:
    ws.world.add_user(CAROL)
    for email, members in groups.items():
        ws.world.add_group(email, members)
    ws.world.add_drive("sd-1", "Board", {ALICE: "organizer", BOB: "writer"}, DOMAIN_ONLY)
    ws.world.add_item("f1", "plan.txt", parent="sd-1", perms=[
        {"type": "group", "role": "writer", "emailAddress": email} for email in groups
    ])


async def test_domain_only_replaces_a_group_holding_an_outsider_with_the_people_of_the_domain(ws: Workspace) -> None:
    domain_drive(ws, {"mixed@example.com": [BOB, CAROL, PARTNER]})
    await ws.sync()
    assert ws.grants("f1") == {(BOB, "USER", "WRITE"), (CAROL, "USER", "WRITE")}


async def test_domain_only_keeps_a_group_whole_when_everyone_in_it_is_of_the_domain(ws: Workspace) -> None:
    domain_drive(ws, {"team@example.com": [BOB, CAROL]})
    await ws.sync()
    assert ws.grants("f1") == {("team@example.com", "GROUP", "WRITE")}


async def test_domain_only_hides_a_group_that_cannot_be_listed(ws: Workspace, caplog: pytest.LogCaptureFixture) -> None:
    domain_drive(ws, {"team@example.com": [BOB, CAROL]})
    ws.world.unlistable_groups["team@example.com"] = 403
    await ws.sync()
    assert ws.grants("f1") == set()
    assert any("team@example.com" in r.getMessage() for r in caplog.records if r.levelname == "WARNING")


async def test_a_restriction_does_not_leave_an_earlier_grant_when_only_a_viewer_reads_back_an_empty_list(
    ws: Workspace,
) -> None:
    ws.world.add_user(CAROL)
    ws.world.add_drive("sd-1", "Board", {ALICE: "organizer", BOB: "reader"})
    ws.world.add_item("f1", "plan.txt", parent="sd-1", perms=[reader(CAROL)])
    await ws.sync()
    assert CAROL in f1_people(ws)

    ws.world.drives["sd-1"]["restrictions"] = MEMBERS_ONLY
    await ws.sync()

    # Bob, a viewer, only ever gets the empty-list fallback; the organizer's read rewrites the grants.
    assert CAROL not in f1_people(ws)


async def test_domain_only_keeps_the_secondary_and_alias_domains_of_the_account(ws: Workspace) -> None:
    ws.world.other_domains = {"example.org": ["alias.example.net"]}
    ws.world.add_drive("sd-1", "Board", {ALICE: "organizer"}, DOMAIN_ONLY)
    ws.world.add_item("f1", "plan.txt", parent="sd-1", perms=[
        reader("zed@example.org"), reader("yan@alias.example.net"), reader(PARTNER),
    ])

    await ws.sync()

    assert f1_people(ws) == {"zed@example.org", "yan@alias.example.net"}


async def test_domains_that_cannot_be_read_fail_closed_and_keep_the_stored_grants(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Board", {ALICE: "organizer"})
    ws.world.add_item("f1", "plan.txt", parent="sd-1", perms=[reader("zed@example.org"), reader(PARTNER)])
    await ws.sync()
    assert f1_people(ws) == {"zed@example.org", PARTNER}

    ws.world.drives["sd-1"]["restrictions"] = DOMAIN_ONLY
    ws.world.domains_unreadable = True
    await ws.sync()
    assert ws.incomplete is True
    assert f1_people(ws) == {"zed@example.org", PARTNER}

    ws.world.domains_unreadable = False
    ws.world.other_domains = {"example.org": []}
    await ws.sync()
    assert f1_people(ws) == {"zed@example.org"}


async def test_a_group_hidden_because_it_could_not_be_listed_comes_back_once_it_can(ws: Workspace) -> None:
    domain_drive(ws, {"team@example.com": [BOB, CAROL]})
    ws.world.unlistable_groups["team@example.com"] = 404
    await ws.sync()
    assert ws.grants("f1") == set()

    del ws.world.unlistable_groups["team@example.com"]
    await ws.sync()
    assert ws.grants("f1") == {("team@example.com", "GROUP", "WRITE")}


def drive_listings(ws: Workspace, since: int) -> int:
    return len([
        r for r in ws.http.requests[since:]
        if r.path == "/drive/v3/files" and r.query.get("corpora") == "drive" and r.query.get("driveId") == "sd-1"
    ])


async def test_a_group_that_stays_unreadable_does_not_relist_the_drive_every_run(ws: Workspace) -> None:
    domain_drive(ws, {"team@example.com": [BOB, CAROL]})
    ws.world.unlistable_groups["team@example.com"] = 404
    await ws.sync()
    assert drive_listings(ws, 0) > 0

    for _ in range(2):
        before = len(ws.http.requests)
        await ws.sync()
        assert drive_listings(ws, before) == 0
    assert ws.grants("f1") == set()


async def test_a_group_that_becomes_unreadable_relists_once_and_its_grant_is_hidden(ws: Workspace) -> None:
    domain_drive(ws, {"team@example.com": [BOB, CAROL]})
    await ws.sync()
    assert ws.grants("f1") == {("team@example.com", "GROUP", "WRITE")}

    ws.world.unlistable_groups["team@example.com"] = 403
    before = len(ws.http.requests)
    await ws.sync()
    assert drive_listings(ws, before) > 0
    assert ws.grants("f1") == set()

    before = len(ws.http.requests)
    await ws.sync()
    assert drive_listings(ws, before) == 0


async def test_a_hidden_group_that_becomes_readable_relists_once_and_access_returns(ws: Workspace) -> None:
    domain_drive(ws, {"team@example.com": [BOB, CAROL]})
    ws.world.unlistable_groups["team@example.com"] = 404
    await ws.sync()

    del ws.world.unlistable_groups["team@example.com"]
    before = len(ws.http.requests)
    await ws.sync()
    assert drive_listings(ws, before) > 0
    assert ws.grants("f1") == {("team@example.com", "GROUP", "WRITE")}

    before = len(ws.http.requests)
    await ws.sync()
    assert drive_listings(ws, before) == 0


async def test_two_hidden_groups_one_recovers_then_the_other(ws: Workspace) -> None:
    domain_drive(ws, {"a@example.com": [BOB], "b@example.com": [CAROL]})
    ws.world.unlistable_groups.update({"a@example.com": 404, "b@example.com": 403})
    await ws.sync()
    assert ws.grants("f1") == set()

    del ws.world.unlistable_groups["a@example.com"]
    before = len(ws.http.requests)
    await ws.sync()
    assert drive_listings(ws, before) > 0
    assert ws.grants("f1") == {("a@example.com", "GROUP", "WRITE")}

    before = len(ws.http.requests)
    await ws.sync()
    assert drive_listings(ws, before) == 0  # b is still unreadable and still remembered
    assert ws.grants("f1") == {("a@example.com", "GROUP", "WRITE")}

    del ws.world.unlistable_groups["b@example.com"]
    before = len(ws.http.requests)
    await ws.sync()
    assert drive_listings(ws, before) > 0
    assert ws.grants("f1") == {("a@example.com", "GROUP", "WRITE"), ("b@example.com", "GROUP", "WRITE")}


def join_group(ws: Workspace, group: str, email: str) -> None:
    ws.world.groups[group]["members"].append(email)


def leave_group(ws: Workspace, group: str, email: str) -> None:
    ws.world.groups[group]["members"].remove(email)


async def test_members_only_a_non_member_who_joins_a_kept_group_gets_nothing(ws: Workspace) -> None:
    ws.world.add_user(CAROL)
    ws.world.add_group("eng@example.com", [BOB])
    ws.world.add_group("kept@example.com", [BOB])
    ws.world.add_drive("sd-1", "Board", {ALICE: "organizer", "eng@example.com": "reader"}, MEMBERS_ONLY)
    ws.world.add_item("f1", "plan.txt", parent="sd-1", perms=[
        {"type": "group", "role": "reader", "emailAddress": "kept@example.com"},
    ])
    await ws.sync()
    assert "kept@example.com" in f1_people(ws) and CAROL not in f1_people(ws)

    join_group(ws, "kept@example.com", CAROL)
    before = len(ws.http.requests)
    await ws.sync()

    assert drive_listings(ws, before) > 0
    assert "kept@example.com" not in f1_people(ws) and CAROL not in f1_people(ws) and BOB in f1_people(ws)


async def relisted(ws: Workspace) -> bool:
    before = len(ws.http.requests)
    await ws.sync()
    return drive_listings(ws, before) > 0


async def test_an_outsider_joining_a_group_kept_whole_loses_the_group_grant_on_the_next_run(ws: Workspace) -> None:
    domain_drive(ws, {"team@example.com": [BOB, CAROL]})
    await ws.sync()
    assert ws.grants("f1") == {("team@example.com", "GROUP", "WRITE")}

    join_group(ws, "team@example.com", PARTNER)

    assert await relisted(ws)
    assert ws.grants("f1") == {(BOB, "USER", "WRITE"), (CAROL, "USER", "WRITE")}


async def test_a_person_leaving_an_expanded_group_loses_their_individual_grant(ws: Workspace) -> None:
    domain_drive(ws, {"mixed@example.com": [BOB, CAROL, PARTNER]})
    await ws.sync()
    assert ws.grants("f1") == {(BOB, "USER", "WRITE"), (CAROL, "USER", "WRITE")}

    leave_group(ws, "mixed@example.com", CAROL)

    assert await relisted(ws)
    assert ws.grants("f1") == {(BOB, "USER", "WRITE")}


async def test_a_person_leaving_a_member_group_loses_their_direct_share(ws: Workspace) -> None:
    ws.world.add_user(CAROL)
    ws.world.add_group("eng@example.com", [BOB, CAROL])
    ws.world.add_drive("sd-1", "Board", {ALICE: "organizer", "eng@example.com": "writer"}, MEMBERS_ONLY)
    ws.world.add_item("f1", "plan.txt", parent="sd-1", perms=[
        {"type": "user", "role": "writer", "emailAddress": BOB},
        {"type": "user", "role": "writer", "emailAddress": CAROL},
    ])
    await ws.sync()
    assert f1_people(ws) == {BOB, CAROL}

    leave_group(ws, "eng@example.com", CAROL)

    assert await relisted(ws)
    assert f1_people(ws) == {BOB}


async def test_groups_that_did_not_change_cost_no_relist(ws: Workspace) -> None:
    domain_drive(ws, {"team@example.com": [BOB, CAROL]})
    await ws.sync()
    assert not await relisted(ws)
    assert not await relisted(ws)
    assert ws.grants("f1") == {("team@example.com", "GROUP", "WRITE")}


async def test_an_unrestricted_drive_records_nothing_about_its_groups(ws: Workspace) -> None:
    ws.world.add_group("team@example.com", [BOB, CAROL])
    ws.world.add_user(CAROL)
    ws.world.add_drive("sd-1", "Board", {ALICE: "organizer", BOB: "writer"})
    ws.world.add_item("f1", "plan.txt", parent="sd-1", perms=[
        {"type": "group", "role": "writer", "emailAddress": "team@example.com"},
    ])
    await ws.sync()
    join_group(ws, "team@example.com", PARTNER)
    assert not await relisted(ws)
    assert not [w for k, w in ws.sync_points.writes if "shared_drive_restrictions_sd-1" in k and w.get("group_hashes")]


# --- discoverable shares: the rest of the lifecycle ---------------------------

async def test_a_suspended_person_is_in_the_audience_again_when_reinstated(ws: Workspace) -> None:
    await ws.sync()
    ws.world.users[BOB].suspended = True
    await ws.sync()
    assert BOB not in {m.email for m in ws.records.user_groups["workspace-members"][1]}
    ws.world.users[BOB].suspended = False
    await ws.sync()
    assert BOB in {m.email for m in ws.records.user_groups["workspace-members"][1]}


async def test_a_share_turned_off_loses_its_audience_grant(ws: Workspace) -> None:
    ws.world.add_item("f", "x.txt", parent="root-alice", owner=ALICE, perms=[discoverable("anyone")])
    await ws.sync()
    assert ("workspace-members", "GROUP", "READ") in ws.grants("f")

    def off() -> None:
        ws.world.files["f"].perms = []

    ws.world._mutate("f", off)
    await ws.sync()
    assert ("workspace-members", "GROUP", "READ") not in ws.grants("f")


async def test_a_limited_item_whose_permissions_cannot_be_read_stops_inheriting_what_a_folder_above_it_shares(ws: Workspace) -> None:
    ws.world.add_item("f", "x.txt", parent="root-alice", owner=ALICE, perms=[discoverable("anyone")])
    await ws.sync()
    assert "f" not in ws.records.stopped_inheriting

    ws.world._mutate("f", lambda: ws.world._bump("f", inheritedPermissionsDisabled=True))
    ws.http.fail("GET", "/drive/v3/files/f/permissions", 500, "backendError")
    await ws.sync()

    assert "f" in ws.records.stopped_inheriting  # known from its metadata: a limited item inherits nothing
    assert any(g[1] == "USER" for g in ws.grants("f"))
    # nothing above it is shared with the audience, so the grant is the item's own share and stays
    assert ("workspace-members", "GROUP", "READ") in ws.grants("f")


async def test_an_unreadable_limited_item_whose_folders_above_cannot_be_read_loses_its_audience_grants(ws: Workspace) -> None:
    ws.world.folder("dir", "Team", parent="root-alice", owner=ALICE)
    ws.world.add_item("f", "x.txt", parent="dir", owner=ALICE, perms=[discoverable("anyone")])
    await ws.sync()

    async def unreadable(record: object) -> list:
        raise RuntimeError("graph unavailable")

    ws.records.get_groups_with_access_to_record = unreadable
    ws.world._mutate("f", lambda: ws.world._bump("f", inheritedPermissionsDisabled=True))
    ws.http.fail("GET", "/drive/v3/files/f/permissions", 500, "backendError")
    await ws.sync()

    # Nothing above shares it, but that could not be read: the grant may have come from above, so it goes.
    assert ("workspace-members", "GROUP", "READ") not in ws.grants("f")
    assert any(g[1] == "USER" for g in ws.grants("f"))


async def test_an_unreadable_limited_item_loses_an_audience_grant_a_folder_above_it_holds_until_it_is_read(
    ws: Workspace,
) -> None:
    ws.world.folder("dir", "Team", parent="root-alice", owner=ALICE, perms=[discoverable("anyone")])
    ws.world.add_item("f", "x.txt", parent="dir", owner=ALICE)
    await ws.sync()
    assert ("workspace-members", "GROUP", "READ") in ws.grants("f"), "the list names what the item inherits"

    ws.world._mutate("f", lambda: ws.world._bump("f", inheritedPermissionsDisabled=True))
    ws.http.fail("GET", "/drive/v3/files/f/permissions", 500, "backendError")
    await ws.sync()

    # the stored grant may be the folder's share, which a limited item no longer gets
    assert ("workspace-members", "GROUP", "READ") not in ws.grants("f")
    assert any(g[1] == "USER" for g in ws.grants("f")), "what else is stored stays"
    assert ("workspace-members", "GROUP", "READ") in ws.grants("dir")


async def test_a_shared_drive_reader_gets_no_grant_of_its_own_when_a_manager_can_list_the_item(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Team", {ALICE: "organizer", BOB: "reader"})
    ws.world.folder("dir", "Plans", parent="sd-1")
    ws.world.add_item("f", "x.txt", parent="dir")
    await ws.sync()

    # Bob reads as a member of the drive: through the folder, so a limit on it cuts him out as Google does.
    assert BOB not in ws.records.perm_emails("f")
    assert BOB not in ws.records.perm_emails("dir")
    assert ws.records.records["f"].inherit_permissions is True


async def test_a_reader_cut_out_by_a_limited_folder_keeps_no_grant_below_it(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Team", {ALICE: "organizer", BOB: "reader"})
    ws.world.folder("dir", "Plans", parent="sd-1")
    ws.world.add_item("f", "x.txt", parent="dir")
    await ws.sync()

    ws.world._mutate("dir", lambda: ws.world._bump("dir", inheritedPermissionsDisabled=True))
    await ws.sync()

    assert ws.records.records["dir"].inherit_permissions is False
    assert BOB not in ws.records.perm_emails("dir")
    assert BOB not in ws.records.perm_emails("f"), "only the folder's own grants and its Managers reach below a limit"


async def test_a_link_shared_item_gives_a_drive_member_no_grant_that_outlasts_a_limited_folder(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Team", {ALICE: "organizer", BOB: "reader"})
    ws.world.folder("dir", "Plans", parent="sd-1")
    ws.world.add_item("f", "x.txt", parent="dir", perms=[{"type": "anyone", "role": "reader", "allowFileDiscovery": False}])
    await ws.sync()
    assert reaches(ws, BOB, "f"), "as a member of the drive"

    ws.world._mutate("dir", lambda: ws.world._bump("dir", inheritedPermissionsDisabled=True))
    await ws.sync()

    assert not reaches(ws, BOB, "f"), "the limit cuts his membership, and a link share names nobody"


async def test_a_direct_grant_removed_from_a_shared_drive_item_goes_though_only_readers_sync_it(ws: Workspace) -> None:
    carol, dana = "carol@example.com", "dana@example.com"
    ws.world.add_user(carol)
    ws.world.add_user(dana)  # in the Workspace, not synced here: only Bob's sync reads the drive
    ws.world.add_drive("sd-1", "Team", {dana: "organizer", BOB: "reader"})
    ws.world.add_item("f", "x.txt", parent="sd-1", perms=[reader(carol)])
    await ws.sync()
    assert carol in ws.records.perm_emails("f")

    ws.world.unshare("f", carol)
    await ws.sync()

    assert carol not in ws.records.perm_emails("f")


async def test_an_unreadable_item_that_is_not_limited_keeps_inheriting(ws: Workspace) -> None:
    ws.world.add_item("f", "x.txt", parent="root-alice", owner=ALICE, perms=[discoverable("anyone")])
    await ws.sync()
    ws.world.rename("f", "y.txt")
    ws.http.fail("GET", "/drive/v3/files/f/permissions", 500, "backendError")
    await ws.sync()
    assert "f" not in ws.records.stopped_inheriting


LABS = "zed@labs.example.org"
XIA = "xia@labs.example.org"
YAN = "yan@labs.example.org"


def two_domains(ws: Workspace, *, reach_all: bool) -> None:
    """An account with a second verified domain, and a file each of Alice and Bob shares with their domain alone."""
    ws.world.other_domains = {"labs.example.org": []}
    for email in (LABS, XIA, YAN):
        ws.world.add_user(email)
    ws.world.domain_share_reaches_all = reach_all
    ws.world.add_item("f", "x.txt", parent="root-alice", owner=ALICE, perms=[discoverable("domain", domain="example.com")])
    ws.world.add_item("f2", "b.txt", parent="root-bob", owner=BOB, perms=[discoverable("domain", domain="example.com")])


def on_labs(email: str | None) -> bool:
    return (email or "").rpartition("@")[2] == "labs.example.org"


def _reach_state(ws: Workspace, domain: str) -> dict | None:
    return ws.sync_points.value(f"domain_share_reach_{domain}")


def reach(ws: Workspace, domain: str = "example.com", other: str = "labs.example.org") -> bool | None:
    """Google's stored answer to whether a share with `domain` reaches the people of `other`; None if it has none."""
    stored = _reach_state(ws, domain)
    return (json.loads(stored["state"])["answers"].get(other) or [None])[0] if stored else None


def yes_by(ws: Workspace, domain: str = "example.com", other: str = "labs.example.org") -> list[str]:
    """The people whose yes is waiting for a later run to agree."""
    stored = _reach_state(ws, domain)
    return (json.loads(stored["state"])["answers"].get(other) or [None, 0, []])[2] if stored else []


async def settle(ws: Workspace) -> None:
    """Three runs: one that finds the files to ask with, one that asks, and one whose other people confirm a yes."""
    for _ in range(3):
        await ws.sync()


def asked_by_labs(ws: Workspace, since: int = 0, ids: tuple[str, ...] = ("f", "f2")) -> list[str]:
    return [f for r in ws.http.requests[since:] if on_labs(r.identity)
            and (f := r.path.rsplit("/", 1)[-1]) in ids]


def reach_domains(ws: Workspace, domain: str = "example.com") -> list[str]:
    return _reach_state(ws, domain)["domains"]


def domain_members(ws: Workspace, domain: str) -> set[str]:
    return {m.email for m in ws.records.user_groups[f"domain-members:{domain}"][1]}


def age(ws: Workspace, domain: str = "example.com", *, days: int | None = None) -> None:
    """Make the stored answers due to be asked again: answered `days` ago, or at the epoch."""
    stored = _reach_state(ws, domain)
    state = json.loads(stored["state"])
    at = 0 if days is None else int(time.time() * 1000) - days * 24 * 60 * 60 * 1000
    state["answers"] = {other: [answer, at, *rest] for other, (answer, _, *rest) in state["answers"].items()}
    stored["state"] = json.dumps(state)


async def test_google_answers_that_a_share_with_a_domain_reaches_the_other_domains_and_its_group_holds_them(ws: Workspace) -> None:
    two_domains(ws, reach_all=True)
    await settle(ws)

    assert ("domain-members:example.com", "GROUP", "READ") in ws.grants("f")
    assert LABS in domain_members(ws, "example.com")
    assert reach(ws) is True and reach_domains(ws) == ["example.com", "labs.example.org"]


async def test_google_answers_that_a_share_with_a_domain_reaches_its_own_people_only(ws: Workspace) -> None:
    two_domains(ws, reach_all=False)
    await settle(ws)

    assert LABS not in domain_members(ws, "example.com")
    assert LABS in domain_members(ws, "labs.example.org")
    assert reach(ws) is False


async def test_each_domain_is_asked_on_its_own(ws: Workspace) -> None:
    two_domains(ws, reach_all=True)
    # Shared with labs.example.org and a person, so it cannot ask about that domain.
    ws.world.add_item("g", "g.txt", parent="root-alice", owner=ALICE, perms=[
        discoverable("domain", domain="labs.example.org"), reader(BOB),
    ])
    await settle(ws)

    assert LABS in domain_members(ws, "example.com")
    assert ("domain-members:labs.example.org", "GROUP", "READ") in ws.grants("g")
    assert ALICE not in domain_members(ws, "labs.example.org"), "an answer for example.com says nothing of labs"
    assert reach(ws, "labs.example.org", "example.com") is None


async def test_an_alias_domain_share_grants_nobody_until_google_answers_for_it(ws: Workspace) -> None:
    ws.world.other_domains = {"labs.example.org": ["alias.example.net"]}
    ws.world.add_user(LABS)
    ws.world.add_item("a", "a.txt", parent="root-alice", owner=ALICE, perms=[
        discoverable("domain", domain="alias.example.net"), reader(BOB),
    ])
    await ws.sync()

    assert ("domain-members:alias.example.net", "GROUP", "READ") in ws.grants("a")
    assert domain_members(ws, "alias.example.net") == set(), "nobody's address is on the alias"


async def test_a_reach_answer_that_cannot_be_read_grants_the_named_domain_only(ws: Workspace) -> None:
    two_domains(ws, reach_all=True)
    await settle(ws)
    assert LABS in domain_members(ws, "example.com")

    read = ws.sync_points.get_sync_point

    async def unreadable(key: str, raise_on_error: bool = False) -> dict | None:
        if key.endswith("domain_share_reach_example.com"):
            raise RuntimeError("store unavailable")
        return await read(key, raise_on_error)

    ws.sync_points.get_sync_point = unreadable
    await ws.sync()
    assert LABS not in domain_members(ws, "example.com")
    assert ("domain-members:example.com", "GROUP", "READ") in ws.grants("f")


async def test_an_unknown_reach_grants_the_named_domain_only_and_is_asked_again(ws: Workspace) -> None:
    two_domains(ws, reach_all=True)
    ws.http.fail("GET", "/drive/v3/files/f", 403, "forbidden", when=lambda r: on_labs(r.identity))
    await settle(ws)

    assert LABS not in domain_members(ws, "example.com"), "a refusal is not an answer"
    assert reach(ws) is None

    ws.http.clear_faults()
    ws.world.rename("f", "y.txt")
    await settle(ws)
    assert LABS in domain_members(ws, "example.com")


async def test_a_file_with_another_route_to_the_other_domain_cannot_ask(ws: Workspace) -> None:
    two_domains(ws, reach_all=False)
    ws.world.perm_page_size = 100  # each list on one page, as the probe needs
    # Each of these lets Zed open the file whatever a domain share reaches, so none of them can ask.
    ws.world.share("f", {"type": "user", "role": "reader", "emailAddress": LABS})
    ws.world.share("f2", {"type": "user", "role": "reader", "emailAddress": LABS})
    ws.world.add_group("labs@example.com", [LABS])
    ws.world.add_item("g", "g.txt", parent="root-alice", owner=ALICE, perms=[
        discoverable("domain", domain="example.com"), {"type": "group", "role": "reader", "emailAddress": "labs@example.com"},
    ])
    ws.world.add_item("h", "h.txt", parent="root-alice", owner=ALICE, perms=[
        discoverable("domain", domain="example.com"), discoverable("anyone"),
    ])
    ws.world.add_item("k", "k.txt", parent="root-alice", owner=ALICE, perms=[
        discoverable("domain", domain="example.com"), discoverable("domain", domain="labs.example.org"),
    ])
    ws.world.add_drive("sd-1", "Team", {ALICE: "organizer", LABS: "reader"})
    ws.world.add_item("d", "d.txt", parent="sd-1", perms=[discoverable("domain", domain="example.com")])
    await settle(ws)

    assert reach(ws) is None
    assert LABS not in domain_members(ws, "example.com")


async def test_a_stale_answer_is_asked_again_and_a_narrower_one_takes_the_other_domains_out(ws: Workspace) -> None:
    two_domains(ws, reach_all=True)
    await settle(ws)
    assert LABS in domain_members(ws, "example.com")

    ws.world.domain_share_reaches_all = False
    await ws.sync()
    assert LABS in domain_members(ws, "example.com"), "a fresh answer is not asked again"

    age(ws)
    await ws.sync()
    assert reach(ws) is False
    assert LABS not in domain_members(ws, "example.com")
    assert ("domain-members:example.com", "GROUP", "READ") in ws.grants("f"), "the file keeps its grant; the group narrows"


async def test_a_wider_answer_lets_the_other_domains_in_without_reading_the_files_again(ws: Workspace) -> None:
    two_domains(ws, reach_all=False)
    await settle(ws)
    assert LABS not in domain_members(ws, "example.com")

    ws.world.domain_share_reaches_all = True
    age(ws)
    await ws.sync()
    assert reach(ws) is None and LABS not in domain_members(ws, "example.com"), "a yes waits for a later run"
    await ws.sync()
    assert reach(ws) is True
    assert LABS in domain_members(ws, "example.com")


async def test_a_new_verified_domain_asks_again_and_reads_everything_again(ws: Workspace) -> None:
    two_domains(ws, reach_all=True)
    ws.world.add_item("n", "n.txt", parent="root-alice", owner=ALICE, perms=[
        discoverable("domain", domain="new.example.net"), reader(BOB),
    ])
    await settle(ws)
    assert reach(ws) is True
    assert {g[1] for g in ws.grants("n")} == {"USER"}, "not a verified domain yet: another organization's"

    ws.world.domain_share_reaches_all = False
    ws.world.other_domains["new.example.net"] = []
    await settle(ws)
    assert reach(ws) is False and reach_domains(ws) == ["example.com", "labs.example.org", "new.example.net"]
    assert LABS not in domain_members(ws, "example.com")
    assert ("domain-members:new.example.net", "GROUP", "READ") in ws.grants("n"), "read again though it did not change"


async def test_a_domain_no_longer_verified_grants_nobody(ws: Workspace) -> None:
    two_domains(ws, reach_all=True)
    ws.world.add_item("g", "g.txt", parent="root-alice", owner=ALICE, perms=[
        discoverable("domain", domain="labs.example.org"), reader(BOB),
    ])
    await ws.sync()
    assert ("domain-members:labs.example.org", "GROUP", "READ") in ws.grants("g")

    ws.world.other_domains = {}
    for email in (LABS, XIA, YAN):
        ws.world.users.pop(email)
    await ws.sync()
    assert domain_members(ws, "labs.example.org") == set()


async def test_a_failed_write_of_the_audience_groups_on_the_first_run_heals(ws: Workspace) -> None:
    ws.world.add_item("f", "x.txt", parent="root-alice", owner=ALICE, perms=[discoverable("anyone")])
    write = ws.records.on_new_user_groups

    async def refuse_audience(groups: list) -> None:
        if any(g.source_user_group_id == "workspace-members" for g, _ in groups):
            raise RuntimeError("memory pool refused")
        await write(groups)

    ws.records.on_new_user_groups = refuse_audience
    await ws.sync()
    assert ws.incomplete is True
    assert "f" not in ws.records.records or ("workspace-members", "GROUP", "READ") not in ws.grants("f")

    ws.records.on_new_user_groups = write
    await ws.sync()
    assert ("workspace-members", "GROUP", "READ") in ws.grants("f"), "the item was held and read again"


async def test_a_discoverable_share_in_another_organizations_shared_drive_grants_no_audience(ws: Workspace) -> None:
    ws.world.add_drive("sd-x", "Partner", {ALICE: "reader"})
    ws.world.drives["sd-x"]["foreign"] = True
    ws.world.add_item("x", "x.txt", parent="sd-x", perms=[discoverable("anyone"), reader(ALICE)])
    ws.world.files["x"].perm_access = "all"  # Alice sees the whole sharing list
    await ws.sync()

    assert "x" in ws.records.records
    assert not any(g[1] == "GROUP" for g in ws.grants("x"))


async def test_a_domain_whose_people_are_all_gone_is_written_empty(ws: Workspace) -> None:
    ws.world.add_user("zed@gone.example")
    await ws.sync()
    assert "domain-members:gone.example" in ws.records.user_groups
    ws.world.users.pop("zed@gone.example")
    await ws.sync()
    assert ws.records.user_groups["domain-members:gone.example"][1] == []


async def test_a_domain_share_whose_domains_cannot_be_read_fails_the_item_and_keeps_its_stored_grants(ws: Workspace) -> None:
    ws.world.add_item("f", "x.txt", parent="root-alice", owner=ALICE, perms=[discoverable("domain", domain="example.com")])
    ws.world.add_item("g", "y.txt", parent="root-alice", owner=ALICE, perms=[discoverable("anyone")])
    await ws.sync()
    assert ("domain-members:example.com", "GROUP", "READ") in ws.grants("f")

    # A refusal, not only an outage: a refused domain list must not read as a refused permission list.
    ws.http.fail("GET", "/admin/directory/v1/customer/[^/]+/domains", 403, "forbidden")
    ws.world.share("f", {"type": "user", "role": "reader", "emailAddress": BOB})
    ws.world.share("g", {"type": "user", "role": "reader", "emailAddress": BOB})
    await ws.sync()

    assert ws.incomplete is True
    assert ("domain-members:example.com", "GROUP", "READ") in ws.grants("f"), "the stored grants stay until a read succeeds"
    assert (BOB, "USER", "READ") not in ws.grants("f")
    assert (BOB, "USER", "READ") in ws.grants("g"), "an item without a domain share does not need the domains"

    ws.http.clear_faults()
    await ws.sync()
    assert {("domain-members:example.com", "GROUP", "READ"), (BOB, "USER", "READ")} <= ws.grants("f")


async def test_a_file_deleted_from_a_filtered_out_drive_is_removed_though_a_discoverable_share_names_the_audience(
    ws: Workspace,
) -> None:
    ws.world.add_drive("sd-2", "Finance", {ALICE: "organizer"})
    ws.world.add_item("f1", "fin.txt", parent="sd-2", perms=[
        {"type": "user", "role": "writer", "emailAddress": BOB}, discoverable("domain", domain="example.com"),
    ])
    ws.filters(drive_ids={"operator": "not_in", "type": "list", "value": ["sd-2"]})
    await ws.sync()
    assert ("domain-members:example.com", "GROUP", "READ") in ws.grants("f1")

    ws.world.delete("f1")
    await ws.sync()
    assert "f1" not in ws.records.records


async def test_a_reindex_on_a_fresh_instance_keeps_the_audience_grant(ws: Workspace) -> None:
    ws.world.add_item("f", "x.txt", parent="root-alice", owner=ALICE, perms=[discoverable("anyone")])
    await ws.sync()
    old = ws.connector
    ws.connector = None
    fresh = await ws.connector_()
    assert not fresh._audience_groups
    ws.world.share("f", {"type": "user", "role": "reader", "emailAddress": BOB})

    await fresh.reindex_records([ws.records.records["f"]])

    assert ("workspace-members", "GROUP", "READ") in ws.grants("f")
    assert (BOB, "USER", "READ") in ws.grants("f")
    await old.cleanup()


async def test_a_failed_audience_write_does_not_stop_the_sync_and_keeps_the_stored_groups(ws: Workspace) -> None:
    ws.world.add_item("f", "x.txt", parent="root-alice", owner=ALICE, perms=[discoverable("anyone")])
    await ws.sync()
    stored_members = list(ws.records.user_groups["workspace-members"][1])
    write = ws.records.on_new_user_groups

    async def refuse_audience(groups: list) -> None:
        if any(g.source_user_group_id == "workspace-members" for g, _ in groups):
            raise RuntimeError("memory pool refused")
        await write(groups)

    ws.records.on_new_user_groups = refuse_audience
    ws.world.add_item("g", "y.txt", parent="root-alice", owner=ALICE, perms=[discoverable("anyone")])
    ws.world.add_user("cara@example.com")  # the group changes, so it is written
    await ws.sync()

    assert ws.incomplete is True, "reported, and tried again next run"
    assert "g" in ws.records.records
    assert ("workspace-members", "GROUP", "READ") in ws.grants("g"), "the stored group is still granted"
    assert ws.records.user_groups["workspace-members"][1] == stored_members


async def test_one_owners_files_alone_do_not_widen_a_domain_share(ws: Workspace) -> None:
    two_domains(ws, reach_all=True)
    ws.world.add_item("f3", "c.txt", parent="root-alice", owner=ALICE, perms=[discoverable("domain", domain="example.com")])
    ws.world.files["f2"].perms = [reader(ALICE)]  # Bob's file can no longer ask
    await settle(ws)

    assert reach(ws) is None, "one owner can open each of their files to the person asked"
    assert LABS not in domain_members(ws, "example.com")


async def test_a_share_given_to_the_person_asked_while_asking_is_not_an_answer(ws: Workspace) -> None:
    two_domains(ws, reach_all=False)

    def share_with_the_person(req: object) -> bool:
        file_id = req.path.rsplit("/", 1)[-1]
        who = req.identity or ""
        if on_labs(who) and file_id in ("f", "f2") and not ws.world.can_open(file_id, who):
            ws.world.files[file_id].perms.append(reader(who))
        return False

    ws.http.fail("GET", "/drive/v3/files/f2?$|/drive/v3/files/f$", 500, None, when=share_with_the_person)
    await settle(ws)

    assert reach(ws) is None, "the owner reads the file again after the person, and it changed"
    assert yes_by(ws) == [], "not even a yes waiting for another run"
    assert LABS not in domain_members(ws, "example.com")


async def test_a_file_at_the_top_of_its_owners_drive_is_asked_with_first(ws: Workspace) -> None:
    two_domains(ws, reach_all=True)
    ws.world.folder("dir", "Team", parent="root-alice", owner=ALICE)
    ws.world.add_item("e", "e.txt", parent="dir", owner=ALICE, perms=[discoverable("domain", domain="example.com")])
    await settle(ws)

    asked = asked_by_labs(ws, ids=("f", "f2", "e"))
    assert asked[:2] == ["f", "f2"] or asked[:2] == ["f2", "f"], asked
    assert "e" not in asked, "two owners answered before it was needed"


async def test_the_person_asked_is_picked_at_random_and_a_yes_is_confirmed_by_someone_else(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.connectors.sources.google.drive.team.connector as drive_connector

    two_domains(ws, reach_all=True)
    offered: list[list[str]] = []

    def choice(people: list) -> object:
        offered.append(sorted(u.email for u in people))
        return people[-1]

    monkeypatch.setattr(drive_connector.secrets, "choice", choice)
    await settle(ws)

    assert offered[0] == [XIA, YAN, LABS], "everyone on the other domain who can answer"
    assert offered[-1] == [XIA, YAN], "the confirming run asks someone other than who said yes"
    assert reach(ws) is True


async def test_a_person_with_an_address_on_the_shared_domain_is_not_asked(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.connectors.sources.google.drive.team.connector as drive_connector

    two_domains(ws, reach_all=False)
    ws.world.aliases["zed@example.com"] = LABS  # Zed reaches example.com shares through the alias
    monkeypatch.setattr(drive_connector.secrets, "choice", lambda people: people[-1])
    await ws.sync()
    await ws.sync()

    assert reach(ws) is False, "Yan was asked, not Zed"
    assert YAN not in domain_members(ws, "example.com")


async def test_an_answer_for_one_domain_widens_a_share_to_that_domain_only(ws: Workspace) -> None:
    two_domains(ws, reach_all=False)
    ws.world.other_domains["third.example.net"] = []
    ws.world.add_user("tom@third.example.net")
    ws.world.domain_share_reaches = {("example.com", "labs.example.org")}
    await settle(ws)

    assert reach(ws) is True and reach(ws, other="third.example.net") is False
    assert LABS in domain_members(ws, "example.com")
    assert "tom@third.example.net" not in domain_members(ws, "example.com")


async def test_an_audience_group_whose_people_did_not_change_is_not_written_again(ws: Workspace) -> None:
    await ws.sync()
    written: list[str] = []
    write = ws.records.on_new_user_groups

    async def record(groups: list) -> None:
        written.extend(g.source_user_group_id for g, _ in groups)
        await write(groups)

    ws.records.on_new_user_groups = record
    await ws.sync()
    assert not [g for g in written if g.startswith(("workspace-members", "domain-members:"))]

    ws.world.users[BOB].suspended = True
    await ws.sync()
    assert {"workspace-members", "domain-members:example.com"} <= set(written)
    assert BOB not in domain_members(ws, "example.com")


async def test_a_group_written_part_way_is_written_again_when_the_answer_goes_back(ws: Workspace) -> None:
    two_domains(ws, reach_all=False)
    await settle(ws)
    assert LABS not in domain_members(ws, "example.com")
    write = ws.records.on_new_user_groups

    async def store_then_fail(groups: list) -> None:
        await write(groups)
        raise RuntimeError("connection lost after the write")

    ws.records.on_new_user_groups = store_then_fail
    ws.world.domain_share_reaches_all = True
    age(ws)
    await ws.sync()
    await ws.sync()  # the yes is confirmed, and the wider group stored before the write fails
    assert ws.incomplete is True and LABS in domain_members(ws, "example.com")

    ws.records.on_new_user_groups = write
    ws.world.domain_share_reaches_all = False
    age(ws)
    await ws.sync()
    assert reach(ws) is False
    assert LABS not in domain_members(ws, "example.com"), "the narrow group is written though it was before"


async def test_a_file_first_seen_this_run_is_not_asked_with(ws: Workspace) -> None:
    two_domains(ws, reach_all=True)
    await ws.sync()
    assert asked_by_labs(ws) == [], "a file is asked with only once a later run finds it unchanged"
    assert reach(ws) is None


async def test_a_file_that_changed_since_the_earlier_run_is_not_asked_with(ws: Workspace) -> None:
    two_domains(ws, reach_all=True)
    await ws.sync()


    def new_role() -> None:  # still the domain's alone
        ws.world.files["f"].perms = [discoverable("domain", domain="example.com", role="commenter")]

    ws.world._mutate("f", new_role)
    before = len(ws.http.requests)
    await ws.sync()
    # Seen changed at the start of this run, and read again later in it: still not asked with until a later run.
    assert "f" not in asked_by_labs(ws, before) and "f2" in asked_by_labs(ws, before)


async def test_a_yes_that_another_person_does_not_repeat_on_a_later_run_does_not_widen(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.connectors.sources.google.drive.team.connector as drive_connector

    two_domains(ws, reach_all=False)
    monkeypatch.setattr(drive_connector.secrets, "choice", lambda people: people[-1])
    await ws.sync()
    # Someone briefly shares both files by link while Zed is asked, and takes it away before the owner looks again.
    can_open = ws.world.can_open
    ws.world.can_open = lambda file_id, email: email == LABS and file_id in ("f", "f2") or can_open(file_id, email)
    await ws.sync()
    assert yes_by(ws) == [LABS] and LABS not in domain_members(ws, "example.com"), "one run's yes is not enough"

    ws.world.can_open = can_open
    await ws.sync()
    assert reach(ws) is False, "Yan, asked next, cannot open them"
    assert LABS not in domain_members(ws, "example.com")


async def test_audience_groups_are_all_written_again_after_a_day(ws: Workspace) -> None:
    await ws.sync()
    group, members = ws.records.user_groups["workspace-members"]
    ws.records.user_groups["workspace-members"] = (group, [m for m in members if m.email != BOB])  # removed in the store only
    await ws.sync()
    assert BOB not in {m.email for m in ws.records.user_groups["workspace-members"][1]}, "unchanged at the source: skipped"

    ws.sync_points.value("audience_group_digests")["written_at"] = 0
    await ws.sync()
    assert BOB in {m.email for m in ws.records.user_groups["workspace-members"][1]}


async def test_the_audience_people_asked_are_picked_at_random(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.connectors.sources.google.drive.team.connector as drive_connector

    for email in ("a1@example.com", "a2@example.com", "a3@example.com", "a4@example.com"):
        ws.world.add_user(email)
        ws.records.active_users = [*ws.records.active_users, User(id=f"u-{email}", email=email, is_active=True)]
    _audience_file_in_an_unwalked_drive(ws, discoverable("anyone"))
    await ws.sync()
    pools: list[list[str]] = []
    chosen: set[str] = set()

    class Picker:
        def sample(self, pool: list, k: int) -> list:
            pools.append(sorted(u.email for u in pool))
            chosen.update(u.email for u in pool[-k:])
            return pool[-k:]

    monkeypatch.setattr(drive_connector.secrets, "SystemRandom", Picker)
    ws.world.files["g1"].perms = [p for p in ws.world.files["g1"].perms if p.get("type") != "anyone"]
    ws.world.unshare("g1", BOB)
    before = len(ws.http.requests)
    await ws.sync()

    assert pools and BOB not in pools[0] and len(pools[0]) > 3
    asked = {r.identity for r in ws.http.requests[before:] if r.path.endswith("/files/g1") and r.identity != BOB}
    assert len(chosen) == 3 and asked == chosen, "only the people picked are asked"
    assert "g1" not in ws.records.records


async def test_a_refusal_from_every_audience_person_asked_is_not_a_no(ws: Workspace) -> None:
    _audience_file_in_an_unwalked_drive(ws, discoverable("anyone"))
    await ws.sync()
    ws.world.unshare("g1", BOB)
    # Still shared with everyone, but the people asked are refused (their own settings), not told it is not there.
    ws.http.fail("GET", "/drive/v3/files/g1", 403, "insufficientFilePermissions", when=lambda r: r.identity != BOB)
    await ws.sync()
    assert "g1" in ws.records.records and ws.incomplete is True


def audience_members(ws: Workspace, group_id: str) -> set[str]:
    return {m.email for m in ws.records.user_groups[group_id][1]}


async def test_a_cut_user_listing_that_leaves_out_a_member_keeps_them_in_the_audience(ws: Workspace) -> None:
    ws.world.add_item("f", "x.txt", parent="root-alice", owner=ALICE, perms=[discoverable("anyone")])
    await ws.sync()
    assert BOB in audience_members(ws, "workspace-members")

    ws.world.cut_user_listing = {BOB}
    await ws.sync()
    assert ws.incomplete is True
    assert BOB in audience_members(ws, "workspace-members"), "left out of a cut listing is unknown, not gone"
    assert BOB in domain_members(ws, "example.com")
    assert ("workspace-members", "GROUP", "READ") in ws.grants("f")

    ws.world.cut_user_listing = None
    ws.world.users[BOB].suspended = True
    await ws.sync()
    assert BOB not in audience_members(ws, "workspace-members"), "the next complete listing writes the groups exactly"


async def test_a_cut_user_listing_that_shows_someone_suspended_takes_them_out_of_the_audience(ws: Workspace) -> None:
    await ws.sync()
    ws.world.cut_user_listing = {ALICE}
    ws.world.users[BOB].suspended = True
    await ws.sync()
    assert BOB not in audience_members(ws, "workspace-members") and BOB not in domain_members(ws, "example.com")
    assert ALICE in audience_members(ws, "workspace-members")


async def test_a_reach_answer_that_narrows_on_a_cut_listing_takes_the_other_domains_out(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.connectors.sources.google.drive.team.connector as drive_connector

    # Fixed picks, so someone the cut listing shows and who did not say yes is left to ask: random ones could leave
    # nobody, and a check that learns nothing gives no "no" (the group narrows all the same).
    monkeypatch.setattr(drive_connector.secrets, "choice", lambda people: people[-1])
    two_domains(ws, reach_all=True)
    await settle(ws)
    assert {LABS, XIA, YAN} <= domain_members(ws, "example.com")

    ws.world.domain_share_reaches_all = False
    age(ws)
    ws.world.cut_user_listing = {ADMIN, XIA}
    await ws.sync()
    assert reach(ws) is False
    assert not {LABS, XIA, YAN} & domain_members(ws, "example.com"), "Xia too, though the cut listing left her out"
    assert ALICE in domain_members(ws, "example.com")


def _probe_files(ws: Workspace, domain: str = "example.com") -> list[str]:
    return [c["file_id"] for c in json.loads(_reach_state(ws, domain)["state"])["candidates"]]


async def test_a_recheck_that_learns_nothing_keeps_a_yes_until_it_is_too_old(ws: Workspace) -> None:
    two_domains(ws, reach_all=True)
    await settle(ws)
    assert reach(ws) is True and LABS in domain_members(ws, "example.com")
    said_yes = yes_by(ws)

    # Every person asked times out: nothing is learned, so the yes and who said it stay, and it is asked again.
    ws.http.fail("GET", "/drive/v3/files/f2?$|/drive/v3/files/f$", 500, "backendError",
                 when=lambda r: on_labs(r.identity))
    age(ws, days=8)
    await ws.sync()
    assert reach(ws) is True and yes_by(ws) == said_yes
    assert LABS in domain_members(ws, "example.com"), "a timeout is not a no"

    age(ws, days=31)
    await ws.sync()
    assert reach(ws) is None and yes_by(ws) == []
    assert LABS not in domain_members(ws, "example.com"), "a yes that could not be asked again for a month narrows"
    assert ("domain-members:example.com", "GROUP", "READ") in ws.grants("f")

    ws.http.clear_faults()
    await ws.sync()
    await ws.sync()
    assert reach(ws) is True and LABS in domain_members(ws, "example.com")


async def test_probe_files_that_are_gone_make_room_for_new_ones(ws: Workspace) -> None:
    two_domains(ws, reach_all=True)
    for email in (CAROL, "dave@example.com"):  # people whose Drive is synced
        ws.world.add_user(email)
        ws.records.active_users = [*ws.records.active_users, User(id=f"u-{email}", email=email, is_active=True)]
    for file_id, owner in (("f3", ALICE), ("f4", BOB), ("f5", CAROL), ("f6", CAROL)):
        root = f"root-{owner.split('@')[0]}"
        ws.world.add_item(file_id, f"{file_id}.txt", parent=root, owner=owner, perms=[discoverable("domain", domain="example.com")])
    await settle(ws)
    assert reach(ws) is True and sorted(_probe_files(ws)) == ["f", "f2", "f3", "f4", "f5", "f6"]

    for file_id in ("f", "f2", "f3", "f4", "f5"):
        ws.world.delete(file_id)
    ws.world.trash("f6")
    ws.world.add_item("n", "n.txt", parent="root-dave", owner="dave@example.com", perms=[discoverable("domain", domain="example.com")])
    age(ws, days=8)
    await ws.sync()
    assert LABS in domain_members(ws, "example.com"), "no file left to ask with is not a no"
    assert _probe_files(ws) == ["n"], "files deleted or in the trash are dropped, and the new one is kept"

    before = len(ws.http.requests)
    await ws.sync()
    assert asked_by_labs(ws, before, ids=("n",)), "the weekly check asks with the new file"
    assert reach(ws) is True


async def test_unchanged_audience_groups_are_noted_as_kept_so_a_full_sync_keeps_their_members(ws: Workspace) -> None:
    ws.world.add_item("f", "x.txt", parent="root-alice", owner=ALICE, perms=[discoverable("anyone")])
    await ws.sync()
    ws.records.kept_user_groups.clear()

    await ws.sync()
    assert {"workspace-members", "domain-members:example.com"} <= set(ws.records.kept_user_groups)


async def test_a_reindex_on_a_fresh_instance_keeps_a_shared_drive_items_audience_grant(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Team", {ALICE: "organizer"})
    ws.world.add_item("d", "d.txt", parent="sd-1", perms=[{**reader(ALICE), "role": "writer"}, discoverable("domain", domain="example.com")])
    await ws.sync()
    assert ("domain-members:example.com", "GROUP", "READ") in ws.grants("d")
    old = ws.connector
    ws.connector = None
    fresh = await ws.connector_()
    ws.world.share("d", reader(BOB))

    ws.http.fail("GET", "/drive/v3/drives", 500, "backendError")
    await fresh.reindex_records([ws.records.records["d"]])
    assert ("domain-members:example.com", "GROUP", "READ") in ws.grants("d"), "an unlisted drive is not another organization's"
    assert (BOB, "USER", "READ") not in ws.grants("d"), "the stored grants stay until the item can be read"
    assert ws.sync_points.status_changes[-1][0] == [ws.records.records["d"].id], "left to be retried"

    ws.http.clear_faults()
    await fresh.reindex_records([ws.records.records["d"]])
    assert {("domain-members:example.com", "GROUP", "READ"), (BOB, "USER", "READ")} <= ws.grants("d")
    await old.cleanup()


async def test_a_reindex_on_a_fresh_instance_keeps_a_members_only_drive_to_its_members(ws: Workspace) -> None:
    ws.world.add_drive("sd-m", "Members", {ALICE: "organizer"}, MEMBERS_ONLY)
    ws.world.add_item("m", "m.txt", parent="sd-m", perms=[{**reader(ALICE), "role": "writer"}])
    await ws.sync()
    old = ws.connector
    ws.connector = None
    fresh = await ws.connector_()
    ws.world.share("m", reader(BOB))  # Bob is not a member: the drive's restriction keeps him out

    await fresh.reindex_records([ws.records.records["m"]])

    assert (BOB, "USER", "READ") not in ws.grants("m")
    assert (ALICE, "USER", "WRITE") in ws.grants("m")
    await old.cleanup()


async def test_an_unreadable_domain_list_keeps_a_domain_groups_stored_people_until_it_is_read(ws: Workspace) -> None:
    ws.world.other_domains = {"labs.example.org": ["alias.example.net"]}
    for email in (LABS, XIA, YAN):
        ws.world.add_user(email)
    ws.records.active_users = [*ws.records.active_users, *(User(id=f"u-{e}", email=e, is_active=True) for e in (LABS, XIA))]
    # Nobody's address is on the alias: its group holds only the people a share with it reaches.
    ws.world.domain_share_reaches = {("alias.example.net", "example.com")}
    ws.world.add_item("a1", "a1.txt", parent="root-zed", owner=LABS, perms=[discoverable("domain", domain="alias.example.net")])
    ws.world.add_item("a2", "a2.txt", parent="root-xia", owner=XIA, perms=[discoverable("domain", domain="alias.example.net")])
    await settle(ws)
    assert domain_members(ws, "alias.example.net") == {ADMIN, ALICE, BOB}

    ws.world.domains_unreadable = True
    ws.world.users[BOB].suspended = True
    ws.world.add_user("dave@example.com")
    await ws.sync()
    assert ws.incomplete is True
    assert domain_members(ws, "alias.example.net") == {ADMIN, ALICE}, "unknown is not empty; Bob, shown suspended, goes"

    ws.world.domains_unreadable = False
    await ws.sync()
    assert domain_members(ws, "alias.example.net") == {ADMIN, ALICE, "dave@example.com"}, "written exactly again"


async def test_a_removal_on_a_cut_listing_that_left_out_who_can_open_the_file_waits_for_a_complete_one(ws: Workspace) -> None:
    two_domains(ws, reach_all=False)
    ws.records.active_users = [*ws.records.active_users, User(id="u-zed", email=LABS, is_active=True)]
    ws.world.share("f", reader(LABS))
    await ws.sync()
    assert ("domain-members:example.com", "GROUP", "READ") in ws.grants("f") and (LABS, "USER", "READ") in ws.grants("f")

    # Only Zed is listed; the domain share stays, so Alice and everyone on example.com can still open the file.
    ws.world.cut_user_listing = {ADMIN, ALICE, BOB, XIA, YAN}
    ws.world.unshare("f", LABS)
    held = ws.user_checkpoint(LABS)
    await ws.sync()
    assert "f" in ws.records.records, "people the listing left out were not asked"
    assert ws.incomplete is True and ws.user_checkpoint(LABS) == held, "the change is read again"
    assert (LABS, "USER", "READ") not in ws.grants("f"), "Zed cannot open it: his own grant goes at once"
    assert ("domain-members:example.com", "GROUP", "READ") in ws.grants("f")

    ws.world.cut_user_listing = None
    await ws.sync()
    assert "f" in ws.records.records
    assert (LABS, "USER", "READ") not in ws.grants("f")
    assert ("domain-members:example.com", "GROUP", "READ") in ws.grants("f")
    assert ws.user_checkpoint(LABS) != held


async def test_a_removal_asks_the_people_of_a_domain_a_share_reaches_when_its_own_have_run_out(ws: Workspace) -> None:
    tom = "tom@third.example.net"
    two_domains(ws, reach_all=False)
    ws.world.other_domains["third.example.net"] = []
    ws.world.add_user(tom)
    ws.world.domain_share_reaches = {("example.com", "labs.example.org")}
    ws.world.add_item("g", "g.txt", parent="root-admin", owner=ADMIN, perms=[discoverable("domain", domain="example.com"), reader(tom)])
    ws.records.active_users = [*ws.records.active_users, *(User(id=f"u-{e}", email=e, is_active=True) for e in (tom, LABS, XIA))]
    await settle(ws)
    assert reach(ws) is True and {LABS, XIA} <= domain_members(ws, "example.com")

    # Nobody on example.com uses this deployment any more; the people of labs.example.org still open the file.
    ws.records.active_users = [u for u in ws.records.active_users if u.email in (tom, LABS, XIA)]
    ws.world.unshare("g", tom)
    await ws.sync()
    assert "g" in ws.records.records, "the people the share reaches were asked"
    assert (tom, "USER", "READ") not in ws.grants("g")


async def test_a_removal_nobody_answers_for_takes_the_reporters_own_grant_at_once(ws: Workspace) -> None:
    _audience_file_in_an_unwalked_drive(ws, discoverable("anyone"))
    await ws.sync()
    assert (BOB, "USER", "WRITE") in ws.grants("g1")
    ws.world.unshare("g1", BOB)
    ws.http.fail("GET", "/drive/v3/files/g1", 403, "insufficientFilePermissions", when=lambda r: r.identity != BOB)
    await ws.sync()
    assert "g1" in ws.records.records and ws.incomplete is True
    assert (BOB, "USER", "WRITE") not in ws.grants("g1"), "Bob cannot open it, whatever the others can"
    assert ("workspace-members", "GROUP", "READ") in ws.grants("g1"), "the audience's grant waits"

    ws.http.clear_faults()
    await ws.sync()
    assert "g1" in ws.records.records, "the audience still opens it"
    assert (BOB, "USER", "WRITE") not in ws.grants("g1")


async def test_a_shared_drive_removal_on_a_cut_listing_takes_the_reporters_grant_and_waits(ws: Workspace) -> None:
    ws.world.add_drive("sd-1", "Team", {ALICE: "organizer", BOB: "writer"})
    ws.world.add_item("d", "d.txt", parent="sd-1", perms=[{**reader(BOB), "role": "writer"}])
    await ws.sync()
    assert BOB in {g[0] for g in ws.grants("d")}

    # The drive's feed, read by Bob on a run whose listing left out Alice, a member, reports the file removed.
    connector = await ws.connector_()
    connector._listing_cut = True
    connector.synced_users = [u for u in connector.synced_users if u.email == BOB]
    connector.synced_user_emails, connector._deployment_emails = {BOB}, None
    ws.world.delete("d")
    bob = connector.synced_users[0]
    with pytest.raises(RuntimeError, match="left out"):
        await connector._apply_shared_drive_removal("d", bob, "sd-1", await connector._build_user_drive_data_source(bob))
    assert "d" in ws.records.records, "Alice was not asked"
    assert BOB not in {g[0] for g in ws.grants("d")}, "Bob's own grant goes at once"

    await ws.sync()
    assert "d" not in ws.records.records, "the next complete listing settles it"


async def test_a_shared_drive_removal_keeps_a_file_a_member_group_nobody_can_list_may_still_open(ws: Workspace) -> None:
    ws.world.add_group("team@example.com", [CAROL])
    ws.world.add_drive("sd-1", "Team", {BOB: "writer", "team@example.com": "reader"})
    ws.world.add_item("d", "d.txt", parent="sd-1", perms=[{**reader(BOB), "role": "writer"}])
    await ws.sync()
    assert BOB in {g[0] for g in ws.grants("d")}

    # Bob leaves the drive, so its feed reports the file removed for him; team@'s members still open it, but the
    # directory will not list them.
    connector = await ws.connector_()
    ws.world.drives["sd-1"]["members"].pop(BOB)
    ws.world.files["d"].perms = []
    ws.world.unlistable_groups["team@example.com"] = 403
    connector._drive_member_emails, connector._group_members_cache = {}, {}
    bob = next(u for u in connector.synced_users if u.email == BOB)
    await connector._apply_shared_drive_removal("d", bob, "sd-1", await connector._build_user_drive_data_source(bob))

    assert "d" in ws.records.records, "members that cannot be listed are not nobody"
    assert BOB not in {g[0] for g in ws.grants("d")}, "Bob's own grant goes"


async def test_a_not_found_from_another_domain_on_a_cut_listing_still_counts_the_people_it_left_out(ws: Workspace) -> None:
    tom = "tom@third.example.net"
    two_domains(ws, reach_all=False)
    ws.world.other_domains["third.example.net"] = []
    ws.world.add_user(tom)
    ws.world.domain_share_reaches = {("example.com", "labs.example.org")}
    ws.world.add_item("g", "g.txt", parent="root-admin", owner=ADMIN, perms=[discoverable("domain", domain="example.com"), reader(tom)])
    ws.records.active_users = [*ws.records.active_users, *(User(id=f"u-{e}", email=e, is_active=True) for e in (tom, LABS, XIA))]
    await settle(ws)
    assert reach(ws) is True and {ALICE, BOB, LABS} <= domain_members(ws, "example.com")

    # The listing leaves out everyone on example.com, who can still open the file; the labs people asked say
    # "not found" (the reach changed, which the stored answer does not know yet).
    ws.world.cut_user_listing = {ADMIN, ALICE, BOB}
    ws.world.domain_share_reaches = set()
    ws.world.unshare("g", tom)
    await ws.sync()
    assert "g" in ws.records.records, "Alice and Bob were not asked"
    assert ws.incomplete is True and (tom, "USER", "READ") not in ws.grants("g")


def _grant_delete_fails_once(ws: Workspace) -> None:
    delete = ws.records.delete_permission_from_record
    failed: list[str] = []

    async def flaky(record_id: str, user_email: str, connector_id: str | None = None) -> None:
        if not failed:
            failed.append(user_email)
            raise RuntimeError("database unavailable while removing a grant")
        await delete(record_id, user_email, connector_id)

    ws.records.delete_permission_from_record = flaky


async def test_a_failed_removal_of_the_reporters_grant_on_a_kept_shared_drive_file_is_tried_again(ws: Workspace) -> None:
    ws.world.add_group("team@example.com", [CAROL])
    ws.world.add_drive("sd-1", "Team", {BOB: "writer", "team@example.com": "reader"})
    ws.world.add_item("d", "d.txt", parent="sd-1", perms=[{**reader(BOB), "role": "writer"}])
    await ws.sync()
    connector = await ws.connector_()
    ws.world.drives["sd-1"]["members"].pop(BOB)
    ws.world.files["d"].perms = []
    ws.world.unlistable_groups["team@example.com"] = 403
    connector._drive_member_emails, connector._group_members_cache = {}, {}
    bob = next(u for u in connector.synced_users if u.email == BOB)
    source = await connector._build_user_drive_data_source(bob)
    _grant_delete_fails_once(ws)

    # Raised, so the drive's checkpoint stays and the change is read again.
    with pytest.raises(RuntimeError, match="removing a grant"):
        await connector._apply_shared_drive_removal("d", bob, "sd-1", source)
    assert "d" in ws.records.records and BOB in {g[0] for g in ws.grants("d")}

    await connector._apply_shared_drive_removal("d", bob, "sd-1", source)
    assert "d" in ws.records.records
    assert BOB not in {g[0] for g in ws.grants("d")}


async def test_a_failed_removal_of_the_reporters_grant_on_an_undecided_my_drive_removal_holds_the_change(ws: Workspace) -> None:
    two_domains(ws, reach_all=False)
    ws.records.active_users = [*ws.records.active_users, User(id="u-zed", email=LABS, is_active=True)]
    ws.world.share("f", reader(LABS))
    await ws.sync()
    ws.world.cut_user_listing = {ADMIN, ALICE, BOB, XIA, YAN}
    ws.world.unshare("f", LABS)
    held = ws.user_checkpoint(LABS)
    _grant_delete_fails_once(ws)
    await ws.sync()
    assert ws.user_checkpoint(LABS) == held and (LABS, "USER", "READ") in ws.grants("f")

    ws.world.cut_user_listing = None
    await ws.sync()
    assert "f" in ws.records.records and (LABS, "USER", "READ") not in ws.grants("f")
    assert ws.user_checkpoint(LABS) != held


async def test_an_undecided_removal_holds_the_users_changes_only_up_to_the_limit_and_is_settled_later(ws: Workspace) -> None:
    from app.connectors.sources.google.drive.utils.folder_filter_utils import (
        MAX_UNRECOGNISED_403_RUNS,
    )

    _audience_file_in_an_unwalked_drive(ws, discoverable("anyone"))
    ws.world.add_drive("sd-9", "Bob's", {BOB: "organizer"})
    ws.world.add_item("s", "s.txt", parent="sd-9", perms=[])
    await ws.sync()
    held = ws.user_checkpoint(BOB)

    ws.world.unshare("g1", BOB)
    # Everyone else asked is refused (Drive off for them, say), for as long as it lasts.
    ws.http.fail("GET", "/drive/v3/files/g1", 403, "insufficientFilePermissions", when=lambda r: r.identity != BOB)
    ws.world.delete("s")  # a later change that only Bob's drive feed applies
    for _ in range(MAX_UNRECOGNISED_403_RUNS - 1):
        await ws.sync()
        assert ws.user_checkpoint(BOB) == held
        assert ("workspace-members", "GROUP", "READ") in ws.grants("g1"), "the record keeps its other grants"
    assert "s" in ws.records.records, "held: Bob's drives are not read yet"

    await ws.sync()
    assert ws.user_checkpoint(BOB) != held, "at the limit the checkpoint moves on"
    assert "s" not in ws.records.records, "so Bob's later changes apply"
    assert "g1" in ws.records.records and (BOB, "USER", "WRITE") not in ws.grants("g1")

    # Checked again on a later run: now a clean answer, nobody can open it.
    ws.http.clear_faults()
    ws.world.files["g1"].perms = [p for p in ws.world.files["g1"].perms if p.get("type") != "anyone"]
    await ws.sync()
    assert "g1" not in ws.records.records


async def test_an_undecided_shared_drive_removal_holds_the_drive_only_up_to_the_limit_and_is_settled_later(
    ws: Workspace,
) -> None:
    from app.connectors.sources.google.drive.utils.folder_filter_utils import (
        MAX_UNRECOGNISED_403_RUNS,
    )

    ws.world.add_drive("sd-1", "Team", {ALICE: "organizer", BOB: "writer"})
    ws.world.add_item("d", "d.txt", parent="sd-1", perms=[])
    await ws.sync()

    def drive_token() -> str | None:
        uid = ws.world.users[BOB].user_id
        return (ws.sync_points.value(f"/{uid}_sd-1") or {}).get("pageToken")

    held = drive_token()
    ws.world.cut_user_listing = {ADMIN, ALICE}  # Alice, who was not asked, stays left out
    ws.world.delete("d")
    ws.world.add_item("e", "e.txt", parent="sd-1", perms=[])  # a later change in the drive
    for _ in range(MAX_UNRECOGNISED_403_RUNS - 1):
        await ws.sync()
        assert drive_token() == held and "d" in ws.records.records

    await ws.sync()
    assert drive_token() != held, "at the limit the drive's checkpoint moves on"
    assert "d" in ws.records.records and "e" in ws.records.records

    ws.world.cut_user_listing = None
    await ws.sync()
    assert "d" not in ws.records.records, "checked again with Alice listed: nobody can open it"


async def _park_an_undecided_removal_of_g1(ws: Workspace) -> None:
    """g1, whose audience nobody asked can answer for, is kept past the limit and listed to be checked again."""
    from app.connectors.sources.google.drive.utils.folder_filter_utils import (
        MAX_UNRECOGNISED_403_RUNS,
    )

    _audience_file_in_an_unwalked_drive(ws, discoverable("anyone"))
    await ws.sync()
    ws.world.unshare("g1", BOB)
    ws.http.fail("GET", "/drive/v3/files/g1", 403, "insufficientFilePermissions", when=lambda r: r.identity != BOB)
    for _ in range(MAX_UNRECOGNISED_403_RUNS):
        await ws.sync()
    assert "g1" in ws.records.records


async def test_a_parked_removal_is_settled_after_a_returning_users_drive_is_read_again_in_full(ws: Workspace) -> None:
    await _park_an_undecided_removal_of_g1(ws)
    connector = await ws.connector_()
    connector._returning_emails = {BOB}
    await connector._restore_returning_users()  # deletes Bob's checkpoints: his next read is a full listing

    ws.http.clear_faults()
    ws.world.files["g1"].perms = [p for p in ws.world.files["g1"].perms if p.get("type") != "anyone"]
    await ws.sync()
    assert "g1" not in ws.records.records, "checked again at the start of the run, though it lists in full"


@pytest.mark.parametrize("reset", ["drive read again", "too many failed items"])
async def test_a_parked_shared_drive_removal_is_settled_after_the_drive_checkpoint_is_deleted(
    ws: Workspace, reset: str
) -> None:
    from app.connectors.core.base.sync_point.sync_point import (
        generate_record_sync_point_key,
    )
    from app.connectors.sources.google.drive.team.connector import MAX_FAILED_ITEMS
    from app.connectors.sources.google.drive.utils.folder_filter_utils import (
        MAX_UNRECOGNISED_403_RUNS,
    )
    from app.models.entities import RecordType

    ws.world.add_user(CAROL)  # in the account, not a user of this deployment
    ws.world.add_drive("sd-1", "Team", {BOB: "writer", CAROL: "reader"})
    ws.world.add_item("d", "d.txt", parent="sd-1", perms=[])
    await ws.sync()
    ws.world.cut_user_listing = {CAROL}  # Carol, who may still open it, is not asked
    ws.world.delete("d")
    for _ in range(MAX_UNRECOGNISED_403_RUNS):
        await ws.sync()
    assert "d" in ws.records.records

    connector = await ws.connector_()
    if reset == "drive read again":
        await connector._read_drive_again("sd-1", reseed=False)
    else:
        bob = next(u for u in connector.synced_users if u.email == BOB)
        key = generate_record_sync_point_key(RecordType.DRIVE.value, "drives", f"{bob.source_user_id}_sd-1")
        assert not await connector._keep_failed_items(key, {str(i) for i in range(MAX_FAILED_ITEMS + 1)})

    ws.world.cut_user_listing = None
    await ws.sync()
    assert "d" not in ws.records.records, "checked again with Carol listed: nobody can open it"


async def _park_undecided_removals(ws: Workspace, *file_ids: str) -> None:
    """Files shared with Bob and "anyone" in a drive no synced user is in, so no listing finds them: Bob loses them
    and nobody else asked can answer (a refusal), so each is kept past the limit and listed, in this order."""
    from app.connectors.sources.google.drive.utils.folder_filter_utils import (
        MAX_UNRECOGNISED_403_RUNS,
    )

    for name in ("a", "b", "c", "d", "e"):  # a listing that finds most records, so a full sync's cleanup runs
        ws.world.add_item(name, f"{name}.txt", parent="root-alice", owner=ALICE)
    _audience_file_in_an_unwalked_drive(ws, discoverable("anyone"))
    for file_id in file_ids:
        if file_id != "g1":
            ws.world.add_item(
                file_id, f"{file_id}.txt", parent="sd-3",
                perms=[{"type": "user", "role": "writer", "emailAddress": BOB}, discoverable("anyone")],
            )
            ws.world.files[file_id].perm_access = "all"
    await ws.sync()
    for file_id in file_ids:
        ws.world.unshare(file_id, BOB)
        # Everyone asked is refused (Drive off for them, say), Bob too, so the "anyone" share answers for nobody.
        ws.http.fail("GET", f"/drive/v3/files/{file_id}", 403, "insufficientFilePermissions")
        for _ in range(MAX_UNRECOGNISED_403_RUNS):
            await ws.sync()
    assert set(file_ids) <= set(ws.records.records)


def _undecided_parts(ws: Workspace) -> list[list[str]]:
    """Bob's My Drive undecided list, part by part (`UNDECIDED_PARTS`, `UNDECIDED_REMOVALS`)."""
    key = f"undecided_removals_{ws.world.users[BOB].user_id}"
    first = ws.sync_points.value(key) or {}
    parts = [first] + [ws.sync_points.value(f"{key}_part{n}") or {} for n in range(1, first.get("parts") or 1)]
    return [[e.partition(":")[0] for e in part.get("entries") or []] for part in parts]


def _read_everything_in_full_next_run(ws: Workspace) -> None:
    from app.connectors.sources.google.drive.team.connector import (
        DISCOVERABLE_SHARES_SYNC_POINT_KEY,
    )

    for key in [k for k in ws.sync_points.sync_points if k.endswith(DISCOVERABLE_SHARES_SYNC_POINT_KEY)]:
        del ws.sync_points.sync_points[key]


def _drop_the_audience_shares(ws: Workspace, *file_ids: str) -> None:
    ws.http.clear_faults()
    for file_id in file_ids:
        ws.world.files[file_id].perms = [p for p in ws.world.files[file_id].perms if p.get("type") != "anyone"]


async def test_an_undecided_list_past_the_cap_keeps_every_file_in_parts_and_settles_a_part_per_run(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.connectors.sources.google.drive.team.connector as drive_connector

    monkeypatch.setattr(drive_connector, "MAX_UNDECIDED_REMOVALS", 1)
    await _park_undecided_removals(ws, "g2", "g1")
    assert _undecided_parts(ws) == [["g2"], ["g1"]], "nothing is dropped past the cap"

    _drop_the_audience_shares(ws, "g1", "g2")
    await ws.sync()
    assert ("g1" in ws.records.records) != ("g2" in ws.records.records), "one part is checked again per run"
    await ws.sync()
    assert "g1" not in ws.records.records and "g2" not in ws.records.records
    assert _undecided_parts(ws) == [[], []]


async def test_an_undecided_audience_file_in_no_listing_is_never_swept_by_a_full_sync(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.connectors.sources.google.drive.team.connector as drive_connector

    monkeypatch.setattr(drive_connector, "MAX_UNDECIDED_REMOVALS", 1)
    await _park_undecided_removals(ws, "g2", "g1")
    _read_everything_in_full_next_run(ws)
    for _ in range(3):
        await ws.sync()
        assert {"g1", "g2"} <= set(ws.records.records), "its audience may still open it: kept while undecided"
        assert ("workspace-members", "GROUP", "READ") in ws.grants("g1")
    assert _undecided_parts(ws) == [["g2"], ["g1"]]
    assert ws.records.deleted == []


async def test_a_full_sync_whose_cleanup_removes_nothing_keeps_the_undecided_files_listed(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.connectors.sources.google.drive.team.connector as drive_connector

    monkeypatch.setattr(drive_connector, "MAX_UNDECIDED_REMOVALS", 1)
    await _park_undecided_removals(ws, "g2", "g1")
    _read_everything_in_full_next_run(ws)
    ws.records.fail_record_listing = True  # the cleanup returns 0 without removing anything
    await ws.sync()
    ws.records.fail_record_listing = False
    assert _undecided_parts(ws) == [["g2"], ["g1"]]

    _drop_the_audience_shares(ws, "g1", "g2")
    await ws.sync()
    await ws.sync()
    assert "g1" not in ws.records.records and "g2" not in ws.records.records, "settled by their recheck"


async def test_a_normal_full_syncs_cleanup_spares_a_file_on_an_undecided_list(ws: Workspace) -> None:
    await _park_undecided_removals(ws, "g1")
    _read_everything_in_full_next_run(ws)
    await ws.sync()
    assert "g1" in ws.records.records and _undecided_parts(ws) == [["g1"]]
    assert "a" in ws.records.records

    _drop_the_audience_shares(ws, "g1")
    await ws.sync()
    assert "g1" not in ws.records.records, "a clean answer settles it"


@pytest.mark.parametrize("bob", ["leaves the drive", "is no longer synced", "leaves the directory"])
async def test_a_parked_shared_drive_removal_is_settled_after_every_synced_member_leaves_the_drive(
    ws: Workspace, bob: str
) -> None:
    from app.connectors.sources.google.drive.utils.folder_filter_utils import (
        MAX_UNRECOGNISED_403_RUNS,
    )

    ws.world.add_user(CAROL)  # in the account, not a user of this deployment
    ws.world.add_drive("sd-1", "Team", {BOB: "writer", CAROL: "reader"})
    ws.world.add_item("d", "d.txt", parent="sd-1", perms=[])
    await ws.sync()
    ws.world.cut_user_listing = {CAROL}  # Carol, who may still open it, is not asked
    ws.world.delete("d")
    for _ in range(MAX_UNRECOGNISED_403_RUNS):
        await ws.sync()
    assert "d" in ws.records.records

    # No synced user walks the drive any more. One no longer synced cannot be asked, and is not.
    if bob == "leaves the drive":
        del ws.world.drives["sd-1"]["members"][BOB]
    elif bob == "leaves the directory":
        ws.world.users.pop(BOB)
        ws.records.active_users = [u for u in ws.records.active_users if u.email != BOB]
    else:
        ws.records.active_users = [u for u in ws.records.active_users if u.email != BOB]
        ws.http.fail("GET", "/drive/v3/files/d", 401, "authError", when=lambda r: r.identity == BOB)
    await ws.sync()
    assert "d" in ws.records.records, "still undecided: Carol is not asked"

    ws.world.cut_user_listing = None
    await ws.sync()
    assert "d" not in ws.records.records, "checked again without Bob's drive run: nobody can open it"


async def test_an_unreadable_undecided_index_makes_the_run_incomplete_and_removes_nothing(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.connectors.sources.google.drive.team.connector import (
        UNDECIDED_REMOVAL_KEYS_SYNC_POINT_KEY,
    )

    await _park_undecided_removals(ws, "g1")
    _read_everything_in_full_next_run(ws)
    ws.world.delete("e")  # a stale record a complete full sync would remove
    real_get = ws.sync_points.get_sync_point

    async def get_sync_point(key: str, raise_on_error: bool = False) -> dict | None:
        if key.endswith(UNDECIDED_REMOVAL_KEYS_SYNC_POINT_KEY):
            raise RuntimeError("store unavailable")
        return await real_get(key, raise_on_error)

    monkeypatch.setattr(ws.sync_points, "get_sync_point", get_sync_point)
    await ws.sync()
    assert ws.incomplete
    assert {"g1", "e"} <= set(ws.records.records) and ws.records.deleted == []


async def test_a_parked_removal_of_a_user_no_longer_synced_is_still_settled(ws: Workspace) -> None:
    await _park_undecided_removals(ws, "g1")
    ws.records.active_users = [u for u in ws.records.active_users if u.email != BOB]  # Bob's runs stop
    await ws.sync()
    assert "g1" in ws.records.records, "still undecided"

    _drop_the_audience_shares(ws, "g1")
    await ws.sync()
    assert "g1" not in ws.records.records, "checked again without Bob's run: nobody can open it"


async def test_a_parked_removal_of_a_user_who_left_the_directory_is_settled_by_everyone_else(ws: Workspace) -> None:
    await _park_undecided_removals(ws, "g1")
    ws.world.users.pop(BOB)  # Bob's account is deleted: nobody can ask as him, or by his id
    ws.records.active_users = [u for u in ws.records.active_users if u.email != BOB]
    _read_everything_in_full_next_run(ws)
    for _ in range(2):
        await ws.sync()
        assert "g1" in ws.records.records, "still undecided: spared by the full sync's cleanup"
        assert ("workspace-members", "GROUP", "READ") in ws.grants("g1")
    assert not any(email == BOB for email, _, _ in ws.grants("g1")), "Bob keeps no grant"

    _drop_the_audience_shares(ws, "g1")
    await ws.sync()
    assert "g1" not in ws.records.records, "settled by the people who may still open it: none can"


def _audience_file_bob_loses(ws: Workspace) -> None:
    """g1, shared with Bob and "anyone" in a drive no synced user is in, so no listing finds it, beside files a
    listing finds, so a full sync's cleanup runs."""
    for name in ("a", "b", "c", "d", "e"):
        ws.world.add_item(name, f"{name}.txt", parent="root-alice", owner=ALICE)
    _audience_file_in_an_unwalked_drive(ws, discoverable("anyone"))


async def test_a_parked_audience_file_its_user_opens_again_survives_a_full_sync(ws: Workspace) -> None:
    from app.connectors.sources.google.drive.utils.folder_filter_utils import (
        MAX_UNRECOGNISED_403_RUNS,
    )

    _audience_file_bob_loses(ws)
    await ws.sync()
    ws.world.unshare("g1", BOB)
    ws.http.fail("GET", "/drive/v3/files/g1", 403, "insufficientFilePermissions", when=lambda r: r.identity != BOB)
    for _ in range(MAX_UNRECOGNISED_403_RUNS):
        await ws.sync()
    assert _undecided_parts(ws) == [["g1"]]
    await ws.sync()  # Bob opens it again through the "anyone" share: the entry leaves the list
    assert _undecided_parts(ws) == [[]]

    ws.http.clear_faults()
    _read_everything_in_full_next_run(ws)
    await ws.sync()
    assert "g1" in ws.records.records, "its audience still opens it"
    assert ("workspace-members", "GROUP", "READ") in ws.grants("g1")


async def test_an_audience_file_kept_without_being_parked_survives_a_full_sync(ws: Workspace) -> None:
    _audience_file_bob_loses(ws)
    await ws.sync()
    ws.world.unshare("g1", BOB)
    await ws.sync()
    assert "g1" in ws.records.records and _undecided_parts(ws) == [[]], "kept at once: its audience opens it"

    _read_everything_in_full_next_run(ws)
    await ws.sync()
    assert "g1" in ws.records.records
    assert ("workspace-members", "GROUP", "READ") in ws.grants("g1")


async def test_an_audience_file_whose_share_is_gone_is_still_removed(ws: Workspace) -> None:
    _audience_file_bob_loses(ws)
    await ws.sync()
    ws.world.unshare("g1", BOB)
    ws.world.files["g1"].perms = [p for p in ws.world.files["g1"].perms if p.get("type") != "anyone"]
    await ws.sync()
    assert "g1" not in ws.records.records, "every person of its audience asked: not found"


async def test_a_full_sync_removes_an_unseen_audience_file_its_audience_can_no_longer_open(ws: Workspace) -> None:
    _audience_file_bob_loses(ws)
    await ws.sync()
    ws.world.unshare("g1", BOB)
    await ws.sync()
    assert "g1" in ws.records.records, "kept: its audience opens it"

    # The share goes without a change anyone's feed reports, and no listing finds the file.
    ws.world.files["g1"].perms = [p for p in ws.world.files["g1"].perms if p.get("type") != "anyone"]
    _read_everything_in_full_next_run(ws)
    await ws.sync()
    assert "g1" not in ws.records.records, "the people of its audience asked: not found"


def _undecided_file_ids(ws: Workspace) -> set[str]:
    """Every file on any undecided list, whoever's."""
    return {
        entry.partition(":")[0]
        for key, value in ws.sync_points.sync_points.items() if "/undecided_removals_" in key
        for entry in value.get("entries") or []
    }


async def _runner_full_sync(ws: Workspace, *, delete_sync_points: bool) -> set[str]:
    """A full sync the runner starts: it may delete every sync point first, and once the run ends unlinks the
    records it neither wrote nor noted kept. Returns those it unlinked."""
    if delete_sync_points:
        ws.sync_points.sync_points.clear()
    else:
        _read_everything_in_full_next_run(ws)
    ws.records.begin_full_sync_run(CONNECTOR_ID)
    await ws.sync()
    assert not ws.incomplete
    return ws.records.unlink_missed_by_full_sync()


async def test_a_runner_full_sync_keeps_an_audience_file_no_listing_shows_linked(ws: Workspace) -> None:
    _audience_file_bob_loses(ws)
    await ws.sync()
    ws.world.unshare("g1", BOB)
    await ws.sync()
    assert "g1" in ws.records.records, "kept: its audience opens it"

    unlinked = await _runner_full_sync(ws, delete_sync_points=True)
    assert ws.records.records["g1"].id not in unlinked
    assert ("workspace-members", "GROUP", "READ") in ws.grants("g1")


async def test_a_runner_full_sync_keeps_a_file_on_an_undecided_list_linked(ws: Workspace) -> None:
    await _park_undecided_removals(ws, "g1")
    unlinked = await _runner_full_sync(ws, delete_sync_points=False)
    assert ws.records.records["g1"].id not in unlinked
    assert ("workspace-members", "GROUP", "READ") in ws.grants("g1")


async def test_a_runner_full_sync_that_deletes_every_sync_point_keeps_an_undecided_file_checked(
    ws: Workspace,
) -> None:
    await _park_undecided_removals(ws, "g1")
    assert _undecided_file_ids(ws) == {"g1"}

    unlinked = await _runner_full_sync(ws, delete_sync_points=True)
    assert "g1" in ws.records.records and ws.records.records["g1"].id not in unlinked
    assert ("workspace-members", "GROUP", "READ") in ws.grants("g1")
    assert _undecided_file_ids(ws) == {"g1"}, "listed again: no one answered for it"

    _drop_the_audience_shares(ws, "g1")
    await ws.sync()
    assert "g1" not in ws.records.records, "checked again: nobody can open it"


@pytest.mark.parametrize("fails", [False, True])
async def test_a_runner_full_sync_pages_the_stored_records_and_unlinks_nothing_when_a_page_fails(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    import app.connectors.sources.google.drive.utils.drive_full_sync_reconciler as reconciler

    monkeypatch.setattr(reconciler, "RECONCILE_PAGE_SIZE", 2)
    _audience_file_bob_loses(ws)
    await ws.sync()
    ws.world.unshare("g1", BOB)
    await ws.sync()
    ws.world.delete("e")  # gone with no change reported: a complete cleanup removes it
    pages: list[str | None] = []
    real_page = ws.records.get_records_by_status

    async def get_records_by_status(*args: object, **kwargs: object) -> list:
        if kwargs.get("is_placeholder") is False:
            pages.append(kwargs.get("after_key"))
            if fails and len(pages) == 3:
                raise RuntimeError("graph unavailable while listing records")
        return await real_page(*args, **kwargs)

    monkeypatch.setattr(ws.records, "get_records_by_status", get_records_by_status)
    _read_everything_in_full_next_run(ws)
    ws.records.begin_full_sync_run(CONNECTOR_ID)
    await ws.sync()
    assert len(pages) >= 3, "read in pages of two"
    if fails:
        assert ws.incomplete, "which unseen records may still exist is not known"
        ws.records.full_sync_seen = None  # the run failed: the runner unlinks nothing
        assert "e" in ws.records.records and ("workspace-members", "GROUP", "READ") in ws.grants("g1")
        return
    assert not ws.incomplete
    unlinked = ws.records.unlink_missed_by_full_sync()
    assert "e" not in ws.records.records
    assert ws.records.records["g1"].id not in unlinked and ("workspace-members", "GROUP", "READ") in ws.grants("g1")


async def test_a_kept_list_file_in_a_drive_a_synced_member_walks_is_settled_only_on_a_clean_answer(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.connectors.sources.google.drive.team.connector import UNDECIDED_KEPT_KEY

    _audience_file_bob_loses(ws)
    await ws.sync()
    ws.world.unshare("g1", BOB)
    await ws.sync()  # kept: its audience opens it
    connector = await ws.connector_()
    await connector._park_undecided_removal(UNDECIDED_KEPT_KEY, "g1:sd-3")  # as a full sync's cleanup lists it

    async def walked(drive_id: str) -> bool:
        return True  # a synced member walks the drive, yet cannot list the file

    monkeypatch.setattr(connector, "_drive_has_synced_member", walked)
    ws.http.fail("GET", "/drive/v3/files/g1", 403, "insufficientFilePermissions")
    await ws.sync()
    assert _undecided_file_ids(ws) == {"g1"}, "asked, and nobody answered: still listed"
    assert ("workspace-members", "GROUP", "READ") in ws.grants("g1")

    _drop_the_audience_shares(ws, "g1")
    await ws.sync()
    assert "g1" not in ws.records.records, "asked again: nobody can open it"
    assert _undecided_file_ids(ws) == set()


async def test_a_kept_list_file_whose_group_members_cannot_be_read_stays_listed(ws: Workspace) -> None:
    from app.connectors.sources.google.drive.team.connector import UNDECIDED_KEPT_KEY

    ws.world.add_user(CAROL)  # in the account, not a user of this deployment: nobody synced answers for the group
    ws.world.add_group("team@example.com", [CAROL])
    _audience_file_bob_loses(ws)
    ws.world.files["g1"].perms.append({"type": "group", "role": "reader", "emailAddress": "team@example.com"})
    await ws.sync()
    ws.world.unshare("g1", BOB)
    await ws.sync()
    connector = await ws.connector_()
    await connector._park_undecided_removal(UNDECIDED_KEPT_KEY, "g1:sd-3")

    ws.world.unlistable_groups["team@example.com"] = 403  # whether its people can open g1 is unknown
    await ws.sync()
    assert _undecided_file_ids(ws) == {"g1"}, "unknown is not kept for good: checked again"


async def _an_audience_file_only_its_audience_opens(ws: Workspace) -> None:
    """g1, in a drive no synced user is in, kept for its "anyone" share after Bob, its only synced holder, lost it;
    the synced users' own Drives are empty."""
    _audience_file_in_an_unwalked_drive(ws, discoverable("anyone"))
    await ws.sync()
    ws.world.unshare("g1", BOB)
    await ws.sync()
    assert "g1" in ws.records.records


async def test_a_runner_full_sync_that_lists_nothing_keeps_an_audience_file_linked(ws: Workspace) -> None:
    await _an_audience_file_only_its_audience_opens(ws)
    unlinked = await _runner_full_sync(ws, delete_sync_points=True)
    assert not (await ws.connector_())._sync_ledger.seen, "a complete listing that found nothing"
    assert ws.records.records["g1"].id not in unlinked
    assert ("workspace-members", "GROUP", "READ") in ws.grants("g1")


async def test_a_runner_full_sync_whose_sync_points_were_not_deleted_keeps_an_audience_file_linked(
    ws: Workspace,
) -> None:
    await _an_audience_file_only_its_audience_opens(ws)
    ws.world.add_item("a", "a.txt", parent="root-alice", owner=ALICE)  # a change the incremental run reads
    ws.records.begin_full_sync_run(CONNECTOR_ID)  # deleting the sync points failed: every feed reads on
    await ws.sync()
    assert not ws.incomplete
    unlinked = ws.records.unlink_missed_by_full_sync()
    assert ws.records.records["g1"].id not in unlinked
    assert ("workspace-members", "GROUP", "READ") in ws.grants("g1")


async def test_a_runner_full_sync_whose_cleanup_raises_unlinks_nothing(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.connectors.sources.google.drive.team.connector as drive_connector

    await _an_audience_file_only_its_audience_opens(ws)

    async def cleanup_fails(*_: object, **__: object) -> int:
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(drive_connector, "reconcile_unseen_records", cleanup_fails)
    ws.sync_points.sync_points.clear()
    ws.records.begin_full_sync_run(CONNECTOR_ID)
    with pytest.raises(RuntimeError):
        await ws.sync()
    # The run failed, so the runner ends it without unlinking (only a run that returns, or ends with
    # IncompleteSyncError, is unlinked after).
    ws.records.full_sync_seen = None
    assert ("workspace-members", "GROUP", "READ") in ws.grants("g1")


async def _park_a_file_of_alices_drive(ws: Workspace, on: str) -> None:
    """f5, in shared drive sd-5 where Alice, a synced user, is a member, whose record holds no edge for her, parked
    as undecided on the list the cleanup keeps or on Bob's own list."""
    from app.connectors.sources.google.drive.team.connector import UNDECIDED_KEPT_KEY

    ws.world.add_drive("sd-5", "Team", {ALICE: "reader"})
    ws.world.add_item("f5", "f5.txt", parent="sd-5", perms=[])
    await ws.sync()
    ws.records.permissions["f5"] = [p for p in ws.records.permissions.get("f5", []) if p.email != ALICE]
    assert (ALICE, "USER", "READ") not in ws.grants("f5")
    connector = await ws.connector_()
    bob = next(u for u in connector.synced_users if u.email == BOB)
    key = UNDECIDED_KEPT_KEY if on == "the kept list" else connector._undecided_key(bob)
    await connector._park_undecided_removal(key, "f5:sd-5")


PARKED_ON = ["the kept list", "a user's list"]


@pytest.mark.parametrize("on", PARKED_ON)
async def test_a_parked_shared_drive_file_a_member_can_open_is_kept(ws: Workspace, on: str) -> None:
    await _park_a_file_of_alices_drive(ws, on)
    await ws.sync()
    assert "f5" in ws.records.records, "Alice, a member of its drive, opens it"
    assert _undecided_file_ids(ws) == set(), "a clean answer: settled"


@pytest.mark.parametrize("on", PARKED_ON)
async def test_a_parked_shared_drive_file_nobody_can_open_is_removed(ws: Workspace, on: str) -> None:
    await _park_a_file_of_alices_drive(ws, on)
    ws.http.fail("GET", "/drive/v3/files/f5", 404, "notFound")
    await ws.sync()
    assert "f5" not in ws.records.records and _undecided_file_ids(ws) == set()


@pytest.mark.parametrize("on", PARKED_ON)
async def test_a_parked_shared_drive_file_its_members_refuse_stays_undecided(ws: Workspace, on: str) -> None:
    await _park_a_file_of_alices_drive(ws, on)
    # Drive turned off for Alice: whether she can open it is unknown.
    ws.http.fail("GET", "/drive/v3/files/f5", 403, "driveServiceDisabled", when=lambda r: r.identity != BOB)
    await ws.sync()
    assert "f5" in ws.records.records and _undecided_file_ids(ws) == {"f5"}
