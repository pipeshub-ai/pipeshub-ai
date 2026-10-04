"""Google Workspace Gmail sync, driven over fake Gmail and Admin Directory APIs.

The connector signs real service-account JWTs (google-auth) to impersonate each user;
the fake token endpoint reads the impersonated subject from them, so every Gmail call
is answered from that user's mailbox only. Admin Directory comes from ``DriveWorld``.
Our databases are in-memory.
"""

import json
import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from drive_world import DriveWorld
from fastapi.responses import StreamingResponse
from gmail_world import GmailWorld
from google_behaviour_fakes import (
    FakeConfigService,
    FakeEntitiesProcessor,
    FakeGoogleHttp,
    FakeSyncPointStore,
)

from app.connectors.sources.google.gmail.team.connector import GoogleGmailTeamConnector
from app.models.entities import RecordType
from app.models.permission import PermissionType

CONNECTOR_ID = "gmail-workspace-1"
ADMIN = "admin@example.com"
ALICE = "alice@example.com"
BOB = "bob@example.com"
OUTSIDER = "Vendor <vendor@elsewhere.com>"


class Harness:
    def __init__(self, http: FakeGoogleHttp, sync_points: FakeSyncPointStore, service_account_info: dict[str, Any]) -> None:
        self.http = http
        self.records = FakeEntitiesProcessor()
        self.sync_points = sync_points
        self.directory = DriveWorld(http)
        self.directory.admin_email = ADMIN
        self.gmail = GmailWorld(http)
        for email in (ADMIN, ALICE, BOB):
            self.directory.add_user(email)
            self.gmail.add_mailbox(email)
        self.records.active_users = [
            SimpleNamespace(email=email, id=f"user-{email.split('@')[0]}", is_active=True) for email in (ALICE, BOB)
        ]
        self.config: dict[str, Any] = {
            "auth": {"serviceAccountJson": json.dumps(service_account_info), "adminEmail": ADMIN},
        }
        self.config_service = FakeConfigService(CONNECTOR_ID, self.config)
        self.connector: Optional[GoogleGmailTeamConnector] = None

    async def connector_(self) -> GoogleGmailTeamConnector:
        if self.connector is None:
            self.connector = GoogleGmailTeamConnector(
                logging.getLogger("gmail-team-behaviour"),
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

    def checkpoint(self, email: str) -> Optional[str]:
        value = self.sync_points.value(f"/{RecordType.MAIL.value}/user/{email}")
        return value.get("historyId") if value else None

    def mail_ids(self) -> set[str]:
        return {r.external_record_id for r in self.records.records.values() if r.record_type == RecordType.MAIL}

    def access(self, external_id: str) -> list[tuple[str, PermissionType]]:
        return [(p.email, p.type) for p in self.records.permissions[external_id]]


@pytest.fixture
async def ws(google_http: FakeGoogleHttp, sync_points: FakeSyncPointStore, service_account_info: dict[str, Any]) -> AsyncIterator[Harness]:
    harness = Harness(google_http, sync_points, service_account_info)
    yield harness
    if harness.connector is not None:
        await harness.connector.cleanup()
    assert not google_http.unrouted, google_http.unrouted


# --- directory ----------------------------------------------------------------


async def test_users_and_groups_are_read_to_the_last_page(ws: Harness) -> None:
    extra = [f"user{n}@example.com" for n in range(3)]
    for email in extra:
        ws.directory.add_user(email)
        ws.gmail.add_mailbox(email)
    ws.directory.add_group("team@example.com", [ALICE, BOB, *extra])
    ws.directory.add_group("empty-ish@example.com", [ALICE])
    ws.directory.add_group("third@example.com", [BOB])

    await ws.sync()

    assert set(ws.records.app_users) == {ADMIN, ALICE, BOB, *extra}
    assert set(ws.records.user_groups) == {"team@example.com", "empty-ish@example.com", "third@example.com"}
    assert {m.email for m in ws.records.user_groups["team@example.com"][1]} == {ALICE, BOB, *extra}


async def test_a_failed_member_read_keeps_the_stored_group_and_the_rest_sync(ws: Harness, backoff_sleeps: list[float]) -> None:
    ws.directory.add_group("team@example.com", [ALICE, BOB])
    ws.directory.add_group("other@example.com", [BOB])
    await ws.sync()
    ws.http.fail("GET", "/admin/directory/v1/groups/team@example.com/members", 500, "backendError")
    ws.directory.groups["other@example.com"]["members"] = [ALICE, BOB]

    await ws.sync()

    assert len(backoff_sleeps) == 3
    assert {m.email for m in ws.records.user_groups["team@example.com"][1]} == {ALICE, BOB}
    assert {m.email for m in ws.records.user_groups["other@example.com"][1]} == {ALICE, BOB}


@pytest.mark.xfail(
    strict=True,
    reason="A group whose last member left is skipped instead of being saved as empty, so the people "
    "who left keep the group's access in PipesHub.",
)
async def test_a_group_whose_members_all_left_is_emptied(ws: Harness) -> None:
    ws.directory.add_group("team@example.com", [ALICE])
    await ws.sync()
    ws.directory.groups["team@example.com"]["members"] = []

    await ws.sync()

    assert ws.records.user_groups["team@example.com"][1] == []


# --- per-user mailboxes and access --------------------------------------------


async def test_each_mailbox_is_read_as_its_owner_and_only_its_owner_gets_access(ws: Harness) -> None:
    ws.gmail.deliver(ALICE, "a-sent", sender=f"Alice <{ALICE}>", to=BOB, subject="Plan")
    ws.gmail.deliver(BOB, "b-recv", sender=f"Alice <{ALICE}>", to=BOB, subject="Plan")
    ws.gmail.deliver(BOB, "b-other", sender=OUTSIDER, attachments=[("invoice.pdf", "application/pdf", b"%PDF")])
    ws.gmail.deliver(ADMIN, "admin-only", sender=OUTSIDER)

    await ws.sync()

    assert ws.mail_ids() == {"a-sent", "b-recv", "b-other"}
    assert ws.access("a-sent") == [(ALICE, PermissionType.OWNER)]
    assert ws.access("b-recv") == [(BOB, PermissionType.READ)]
    assert ws.access("b-other~1") == [(BOB, PermissionType.READ)]
    assert ws.records.records["a-sent"].external_record_group_id == f"{ALICE}:SENT"
    assert ws.records.records["b-recv"].external_record_group_id == f"{BOB}:INBOX"
    assert {ALICE, BOB, ADMIN} <= ws.http.impersonated_subjects()
    assert all(req.identity == BOB for req in ws.http.requests if "/users/bob@example.com/" in req.path)


async def test_a_user_the_service_account_may_not_impersonate_does_not_stop_the_others(ws: Harness) -> None:
    ws.gmail.deliver(ALICE, "a-1", sender=OUTSIDER)
    ws.gmail.deliver(BOB, "b-1", sender=OUTSIDER)
    ws.http.refused_subjects.add(BOB)

    await ws.sync()

    assert ws.mail_ids() == {"a-1"}
    assert ws.checkpoint(ALICE) is not None
    assert ws.checkpoint(BOB) is None


async def test_full_sync_reads_every_thread_page_per_user(ws: Harness) -> None:
    for n in range(5):
        ws.gmail.deliver(ALICE, f"a{n}", sender=OUTSIDER, date_ms=1_700_000_000_000 + n)

    await ws.sync()

    assert ws.mail_ids() == {f"a{n}" for n in range(5)}
    assert len(ws.http.calls("GET", "/gmail/v1/users/alice@example.com/threads")) == 3


@pytest.mark.xfail(
    strict=True,
    reason="A full sync that fails partway saves a history position before it finishes, so the next "
    "run only looks for new mail; that mailbox's older mail (even what was read before the failure, "
    "which was still waiting to be saved) is never synced.",
)
async def test_a_full_sync_that_fails_partway_is_completed_on_the_next_run(ws: Harness) -> None:
    for n in range(5):
        ws.gmail.deliver(ALICE, f"a{n}", sender=OUTSIDER, date_ms=1_700_000_000_000 + n)
    ws.http.fail("GET", "/gmail/v1/users/alice@example.com/threads", 500, "backendError", when=lambda r: r.query.get("pageToken") == "4")

    await ws.sync()
    ws.http.clear_faults()
    await ws.sync()

    assert ws.mail_ids() == {f"a{n}" for n in range(5)}


# --- change tracking ----------------------------------------------------------


async def test_incremental_sync_applies_new_trashed_and_deleted_mail_per_user(ws: Harness) -> None:
    ws.gmail.deliver(ALICE, "a-keep", sender=OUTSIDER)
    ws.gmail.deliver(ALICE, "a-trash", sender=OUTSIDER, attachments=[("x.txt", "text/plain", b"x")])
    ws.gmail.deliver(BOB, "b-delete", sender=OUTSIDER)
    await ws.sync()
    alice_before = ws.checkpoint(ALICE)

    ws.gmail.trash(ALICE, "a-trash")
    ws.gmail.delete(BOB, "b-delete")
    ws.gmail.deliver(ALICE, "a-reply", thread_id="a-keep", sender=f"Alice <{ALICE}>", date_ms=1_800_000_000_000)
    ws.gmail.deliver(BOB, "b-new", sender=OUTSIDER)
    await ws.sync()

    assert ws.mail_ids() == {"a-keep", "a-reply", "b-new"}
    assert "a-trash~1" not in ws.records.records
    assert ws.access("b-new") == [(BOB, PermissionType.READ)]
    assert (ws.records.records["a-keep"].id, ws.records.records["a-reply"].id) in {(f, t) for f, t, _ in ws.records.relations}
    assert int(ws.checkpoint(ALICE)) > int(alice_before)


async def test_an_expired_history_id_falls_back_to_a_full_sync(ws: Harness) -> None:
    ws.gmail.deliver(ALICE, "early", sender=OUTSIDER)
    await ws.sync()
    ws.gmail.deliver(ALICE, "in-the-gap", sender=OUTSIDER)
    ws.gmail.expire_history_before_now(ALICE)

    await ws.sync()

    assert ws.mail_ids() == {"early", "in-the-gap"}
    assert ws.checkpoint(ALICE) == str(ws.gmail.current_history_id(ALICE))


async def test_a_failed_history_read_keeps_the_history_id_and_recovers_next_run(ws: Harness, backoff_sleeps: list[float]) -> None:
    await ws.sync()
    checkpoint = ws.checkpoint(ALICE)
    ws.gmail.deliver(ALICE, "during-outage", sender=OUTSIDER)
    ws.gmail.deliver(BOB, "bob-unaffected", sender=OUTSIDER)
    ws.http.fail("GET", "/gmail/v1/users/alice@example.com/history", 500, "backendError")

    await ws.sync()
    assert len(backoff_sleeps) == 3
    assert ws.checkpoint(ALICE) == checkpoint
    assert "during-outage" not in ws.mail_ids()
    assert "bob-unaffected" in ws.mail_ids()

    ws.http.clear_faults()
    await ws.sync()
    assert "during-outage" in ws.mail_ids()


async def test_history_quota_errors_are_retried_with_backoff(ws: Harness, backoff_sleeps: list[float]) -> None:
    await ws.sync()
    ws.gmail.deliver(ALICE, "a-new", sender=OUTSIDER)
    ws.http.fail("GET", "/gmail/v1/users/alice@example.com/history", 429, "rateLimitExceeded", times=2)

    await ws.sync()

    assert "a-new" in ws.mail_ids()
    assert len(backoff_sleeps) == 2


@pytest.mark.xfail(
    strict=True,
    reason="If saving a user's newly arrived mail to the database fails, the error is logged and that "
    "user's history position still moves forward, so the mail is never retried.",
)
async def test_a_database_failure_saving_new_mail_keeps_the_history_id(ws: Harness) -> None:
    await ws.sync()
    checkpoint = ws.checkpoint(ALICE)
    ws.gmail.deliver(ALICE, "unsaved", sender=OUTSIDER)
    ws.records.fail_writes_for.add("unsaved")

    await ws.sync()

    assert ws.checkpoint(ALICE) == checkpoint


# --- streaming ----------------------------------------------------------------


async def _body(response: StreamingResponse) -> bytes:
    return b"".join([c if isinstance(c, bytes) else c.encode() async for c in response.body_iterator])


async def test_streaming_reads_as_a_user_who_has_the_mail(ws: Harness) -> None:
    ws.gmail.deliver(BOB, "b-1", sender=OUTSIDER, body="<p>Contract terms</p>", attachments=[("terms.txt", "text/plain", b"terms")])
    await ws.sync()
    connector = await ws.connector_()
    ws.http.requests.clear()

    mail_body = await _body(await connector.stream_record(ws.records.records["b-1"]))
    attachment = await _body(await connector.stream_record(ws.records.records["b-1~1"]))

    assert b"Contract terms" in mail_body
    assert attachment == b"terms"
    assert {r.identity for r in ws.http.requests} == {BOB}


async def test_streaming_for_an_unknown_user_is_refused(ws: Harness) -> None:
    ws.gmail.deliver(BOB, "b-1", sender=OUTSIDER)
    await ws.sync()
    connector = await ws.connector_()

    with pytest.raises(Exception) as raised:
        await connector.stream_record(ws.records.records["b-1"], user_id="no-such-user")

    assert getattr(raised.value, "status_code", None) == 403


async def test_reindex_checks_mail_as_its_owner_and_republishes_it(ws: Harness) -> None:
    ws.gmail.deliver(BOB, "b-1", sender=OUTSIDER)
    await ws.sync()
    connector = await ws.connector_()

    await connector.reindex_records([ws.records.records["b-1"]])

    assert [r.external_record_id for r in ws.records.reindexed] == ["b-1"]
