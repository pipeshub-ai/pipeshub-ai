"""Personal Gmail sync, driven over a fake Gmail API with the real Google client.

The connector, ``GoogleClient``, the discovery-built Gmail and Drive services,
``execute()`` with its retry/backoff, google-auth's OAuth credentials and talon's
reply extraction are all real; every HTTP request is answered by ``GmailWorld`` (and
``DriveWorld`` for Drive-linked attachments) and our databases are in-memory.
"""

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any, Optional
from unittest.mock import MagicMock

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
from googleapiclient.errors import HttpError

from app.config.constants.arangodb import RecordRelations
from app.connectors.sources.google.gmail import talon_utils
from app.connectors.sources.google.gmail.individual.connector import (
    GoogleGmailIndividualConnector,
)
from app.models.entities import RecordType
from app.models.permission import PermissionType

CONNECTOR_ID = "gmail-personal-1"
ME = "me@example.com"
CHECKPOINT = f"/{RecordType.MAIL.value}/user/{ME}"
FRIEND = "Friend <friend@elsewhere.com>"


class Harness:
    def __init__(self, http: FakeGoogleHttp, sync_points: FakeSyncPointStore) -> None:
        self.http = http
        self.records = FakeEntitiesProcessor()
        self.sync_points = sync_points
        self.gmail = GmailWorld(http)
        self.gmail.add_mailbox(ME)
        self.gmail.aliases["oauth:refresh-1"] = ME
        self.drive = DriveWorld(http)
        self.drive.add_user(ME)
        self.drive.aliases["oauth:refresh-1"] = ME
        self.http.accept_token("access-1", ME)
        self.config: dict[str, Any] = {
            "auth": {"oauthConfigId": "oauth-app-1", "connectorScope": "personal"},
            "credentials": {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "scope": "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/drive.readonly",
            },
        }
        oauth_app = [{"_id": "oauth-app-1", "config": {"clientId": "client-1", "clientSecret": "secret-1"}}]
        self.config_service = FakeConfigService(CONNECTOR_ID, self.config, {"gmail": oauth_app, "drive": oauth_app})
        self.connector: Optional[GoogleGmailIndividualConnector] = None

    async def connector_(self) -> GoogleGmailIndividualConnector:
        if self.connector is None:
            self.connector = GoogleGmailIndividualConnector(
                logging.getLogger("gmail-individual-behaviour"),
                self.records,
                self.sync_points,
                self.config_service,
                CONNECTOR_ID,
                "personal",
                "user-1",
            )
            assert await self.connector.init()
        return self.connector

    async def sync(self) -> None:
        await (await self.connector_()).run_sync()

    def checkpoint(self) -> Optional[str]:
        value = self.sync_points.value(CHECKPOINT)
        return value.get("historyId") if value else None

    def mail_ids(self) -> set[str]:
        return {r.external_record_id for r in self.records.records.values() if r.record_type == RecordType.MAIL}


@pytest.fixture
async def mail(google_http: FakeGoogleHttp, sync_points: FakeSyncPointStore) -> AsyncIterator[Harness]:
    harness = Harness(google_http, sync_points)
    yield harness
    if harness.connector is not None:
        await harness.connector.cleanup()
    assert not google_http.unrouted, google_http.unrouted


def test_reply_extraction_uses_the_real_talon_library() -> None:
    assert not isinstance(talon_utils.quotations, MagicMock)


# --- full sync ----------------------------------------------------------------


async def test_full_sync_reads_every_thread_page_and_saves_the_history_id_taken_before_listing(mail: Harness) -> None:
    for n in range(5):
        mail.gmail.deliver(ME, f"m{n}", subject=f"Subject {n}", sender=FRIEND, date_ms=1_700_000_000_000 + n)
    mail.gmail.deliver(ME, "sent-1", subject="My note", sender=f"Me <{ME}>", to="friend@elsewhere.com")
    history_before = str(mail.gmail.current_history_id(ME))

    await mail.sync()

    assert mail.mail_ids() == {"m0", "m1", "m2", "m3", "m4", "sent-1"}
    assert len(mail.http.calls("GET", "/gmail/v1/users/me/threads")) == 3
    assert mail.checkpoint() == history_before
    assert [(p.email, p.type) for p in mail.records.permissions["m0"]] == [(ME, PermissionType.READ)]
    assert [(p.email, p.type) for p in mail.records.permissions["sent-1"]] == [(ME, PermissionType.OWNER)]
    assert mail.records.records["m0"].external_record_group_id == f"{ME}:INBOX"
    assert mail.records.records["sent-1"].external_record_group_id == f"{ME}:SENT"
    assert set(mail.records.record_groups) == {f"{ME}:INBOX", f"{ME}:SENT", f"{ME}:OTHERS"}


async def test_messages_in_a_thread_are_linked_in_order(mail: Harness) -> None:
    mail.gmail.deliver(ME, "t-1", thread_id="t", sender=FRIEND, date_ms=1_000)
    mail.gmail.deliver(ME, "t-2", thread_id="t", sender=f"Me <{ME}>", date_ms=2_000)
    mail.gmail.deliver(ME, "t-3", thread_id="t", sender=FRIEND, date_ms=3_000)

    await mail.sync()

    ids = {ext: mail.records.records[ext].id for ext in ("t-1", "t-2", "t-3")}
    assert mail.records.relations == [
        (ids["t-1"], ids["t-2"], RecordRelations.SIBLING.value),
        (ids["t-2"], ids["t-3"], RecordRelations.SIBLING.value),
    ]


async def test_attachments_become_child_records_with_the_mails_access(mail: Harness) -> None:
    mail.drive.add_item("drive-file-1", "big-deck.pdf", parent="root-me", owner=ME, content=b"%PDF-big")
    link = "https://drive.google.com/file/d/drive-file-1/view?usp=drive_web"
    mail.gmail.deliver(
        ME, "with-att", sender=FRIEND, body=f"<p>see <a href='{link}'>deck</a></p>",
        attachments=[("notes.txt", "text/plain", b"attached notes")],
    )

    await mail.sync()

    attachment = mail.records.records["with-att~1"]
    assert attachment.record_name == "notes.txt"
    assert attachment.parent_external_record_id == "with-att"
    assert attachment.external_record_group_id == f"{ME}:INBOX"
    drive_linked = mail.records.records["drive-file-1"]
    assert drive_linked.record_name == "big-deck.pdf"
    assert drive_linked.parent_external_record_id == "with-att"
    for ext in ("with-att~1", "drive-file-1"):
        assert [(p.email, p.type) for p in mail.records.permissions[ext]] == [(ME, PermissionType.READ)]


async def test_received_date_filter_limits_the_full_sync(mail: Harness) -> None:
    mail.gmail.deliver(ME, "old", sender=FRIEND, date_ms=1_600_000_000_000)
    mail.gmail.deliver(ME, "new", sender=FRIEND, date_ms=1_750_000_000_000)
    mail.config["filters"] = {"sync": {"values": {
        "received_date": {"operator": "is_after", "type": "datetime", "value": {"start": 1_700_000_000_000, "end": None}},
    }}}

    await mail.sync()

    assert mail.mail_ids() == {"new"}
    assert mail.http.calls("GET", "/gmail/v1/users/me/threads")[0].query["q"] == "after:1700000000"


async def test_a_thread_deleted_during_the_full_sync_is_skipped_and_the_rest_syncs(mail: Harness) -> None:
    for n in range(3):
        mail.gmail.deliver(ME, f"m{n}", sender=FRIEND)
    mail.http.fail("GET", "/gmail/v1/users/me/threads/m1", 404, "notFound")

    await mail.sync()

    assert mail.mail_ids() == {"m0", "m2"}
    assert mail.checkpoint() is not None


@pytest.mark.xfail(
    strict=True,
    reason="A full sync that fails partway saves a history position before it finishes, so the next "
    "run only looks for new mail; the older mail (even what was read before the failure, which was "
    "still waiting to be saved) is never synced.",
)
async def test_a_full_sync_that_fails_partway_is_completed_on_the_next_run(mail: Harness) -> None:
    for n in range(5):
        mail.gmail.deliver(ME, f"m{n}", sender=FRIEND, date_ms=1_700_000_000_000 + n)
    mail.http.fail("GET", "/gmail/v1/users/me/threads", 500, "backendError", when=lambda r: r.query.get("pageToken") == "4")

    with pytest.raises(HttpError):
        await mail.sync()
    mail.http.clear_faults()
    await mail.sync()

    assert mail.mail_ids() == {f"m{n}" for n in range(5)}


# --- change tracking (history ids) --------------------------------------------


async def test_incremental_sync_adds_new_mail_links_replies_and_advances_the_history_id(mail: Harness) -> None:
    mail.gmail.deliver(ME, "first", thread_id="conv", sender=FRIEND, date_ms=1_000)
    await mail.sync()
    first_checkpoint = mail.checkpoint()

    mail.gmail.deliver(ME, "reply", thread_id="conv", sender=f"Me <{ME}>", date_ms=2_000)
    mail.gmail.deliver(ME, "other", sender=FRIEND, date_ms=3_000)
    await mail.sync()

    assert mail.mail_ids() == {"first", "reply", "other"}
    assert (mail.records.records["first"].id, mail.records.records["reply"].id, RecordRelations.SIBLING.value) in mail.records.relations
    assert int(mail.checkpoint()) > int(first_checkpoint)
    assert mail.checkpoint() == str(mail.gmail.current_history_id(ME))


async def test_mail_to_myself_seen_in_inbox_and_sent_history_is_written_once(mail: Harness) -> None:
    await mail.sync()
    mail.gmail.deliver(ME, "self", sender=f"Me <{ME}>", labels=["INBOX", "SENT"])

    await mail.sync()

    written = [ext for batch in mail.records.new_record_batches for ext in batch]
    assert written.count("self") == 1


async def test_every_history_page_is_read(mail: Harness) -> None:
    await mail.sync()
    for n in range(5):
        mail.gmail.deliver(ME, f"n{n}", sender=FRIEND)

    await mail.sync()

    assert mail.mail_ids() == {f"n{n}" for n in range(5)}
    inbox_pages = [r for r in mail.http.calls("GET", "/gmail/v1/users/me/history") if r.query.get("labelId") == "INBOX"]
    assert len(inbox_pages) == 3


async def test_trashed_and_deleted_mail_is_removed_with_its_attachments(mail: Harness) -> None:
    mail.gmail.deliver(ME, "trash-me", sender=FRIEND, attachments=[("a.txt", "text/plain", b"a")])
    mail.gmail.deliver(ME, "delete-me", sender=FRIEND)
    mail.gmail.deliver(ME, "keep", sender=FRIEND)
    await mail.sync()

    mail.gmail.trash(ME, "trash-me")
    mail.gmail.delete(ME, "delete-me")
    await mail.sync()

    assert mail.mail_ids() == {"keep"}
    assert "trash-me~1" not in mail.records.records


async def test_a_second_run_with_no_new_mail_writes_nothing(mail: Harness) -> None:
    mail.gmail.deliver(ME, "m1", sender=FRIEND)
    await mail.sync()
    checkpoint, batches = mail.checkpoint(), len(mail.records.new_record_batches)

    await mail.sync()

    assert mail.checkpoint() == checkpoint
    assert len(mail.records.new_record_batches) == batches


async def test_a_new_message_that_vanished_before_it_was_read_does_not_stop_the_others(mail: Harness) -> None:
    await mail.sync()
    mail.gmail.deliver(ME, "gone", sender=FRIEND)
    mail.gmail.deliver(ME, "kept", sender=FRIEND)
    mail.http.fail("GET", "/gmail/v1/users/me/messages/gone", 404, "notFound")

    await mail.sync()

    assert mail.mail_ids() == {"kept"}


@pytest.mark.xfail(
    strict=True,
    reason="When reading the inbox's change history fails (an outage or quota that outlasts the "
    "retries), the error is swallowed and the saved history position still moves forward, so the "
    "mail that arrived in that window is never synced.",
)
async def test_a_failed_history_read_does_not_advance_the_history_id(mail: Harness) -> None:
    await mail.sync()
    checkpoint = mail.checkpoint()
    mail.gmail.deliver(ME, "during-outage", sender=FRIEND)
    mail.http.fail("GET", "/gmail/v1/users/me/history", 500, "backendError", when=lambda r: r.query.get("labelId") == "INBOX")

    with contextlib.suppress(Exception):
        await mail.sync()
    assert mail.checkpoint() == checkpoint

    mail.http.clear_faults()
    await mail.sync()
    assert "during-outage" in mail.mail_ids()


@pytest.mark.xfail(
    strict=True,
    reason="When Gmail says the saved history position is too old (HTTP 404), the connector does not "
    "fall back to a full sync; it skips ahead to the current position and the mail in the gap is lost.",
)
async def test_an_expired_history_id_falls_back_to_a_full_sync(mail: Harness) -> None:
    mail.gmail.deliver(ME, "early", sender=FRIEND)
    await mail.sync()
    mail.gmail.deliver(ME, "in-the-gap", sender=FRIEND)
    mail.gmail.expire_history_before_now(ME)

    await mail.sync()

    assert mail.mail_ids() == {"early", "in-the-gap"}


@pytest.mark.xfail(
    strict=True,
    reason="If saving newly arrived mail to the database fails, the error is logged and the history "
    "position still moves forward, so that mail is never retried.",
)
async def test_a_database_failure_saving_new_mail_keeps_the_history_id(mail: Harness) -> None:
    await mail.sync()
    checkpoint = mail.checkpoint()
    mail.gmail.deliver(ME, "unsaved", sender=FRIEND)
    mail.records.fail_writes_for.add("unsaved")

    with contextlib.suppress(Exception):
        await mail.sync()

    assert mail.checkpoint() == checkpoint


# --- rate limits --------------------------------------------------------------


@pytest.mark.parametrize(("status", "reason"), [(429, "rateLimitExceeded"), (403, "userRateLimitExceeded")])
async def test_quota_errors_are_retried_with_backoff(mail: Harness, backoff_sleeps: list[float], status: int, reason: str) -> None:
    mail.gmail.deliver(ME, "m1", sender=FRIEND)
    mail.http.fail("GET", "/gmail/v1/users/me/threads/m1", status, reason, times=2)

    await mail.sync()

    assert mail.mail_ids() == {"m1"}
    assert len(backoff_sleeps) == 2


async def test_a_thread_list_quota_error_that_outlasts_the_retries_fails_without_a_checkpoint(mail: Harness, backoff_sleeps: list[float]) -> None:
    mail.gmail.deliver(ME, "m1", sender=FRIEND)
    mail.http.fail("GET", "/gmail/v1/users/me/threads", 429, "rateLimitExceeded")

    with pytest.raises(HttpError):
        await mail.sync()

    assert len(backoff_sleeps) == 3
    assert mail.checkpoint() is None


# --- tokens -------------------------------------------------------------------


async def test_a_rotated_access_token_in_config_is_used_on_the_next_call(mail: Harness) -> None:
    await mail.sync()
    mail.http.accept_token("access-2", ME)
    mail.config["credentials"]["access_token"] = "access-2"
    mail.gmail.deliver(ME, "m1", sender=FRIEND)

    await mail.sync()

    assert mail.http.requests[-1].headers["authorization"] == "Bearer access-2"
    assert mail.mail_ids() == {"m1"}


async def test_a_rejected_access_token_is_refreshed_with_the_shared_oauth_app(mail: Harness) -> None:
    mail.gmail.deliver(ME, "m1", sender=FRIEND)
    await mail.connector_()
    mail.http._token_owner.pop("access-1")

    await mail.sync()

    assert mail.mail_ids() == {"m1"}
    refresh = [t for t in mail.http.token_requests if t.get("grant_type") == "refresh_token"]
    assert refresh and refresh[0]["refresh_token"] == "refresh-1" and refresh[0]["client_id"] == "client-1"


async def test_a_revoked_refresh_token_fails_the_run_without_a_checkpoint(mail: Harness) -> None:
    mail.gmail.deliver(ME, "m1", sender=FRIEND)
    await mail.connector_()
    mail.http._token_owner.pop("access-1")
    mail.http.revoked_refresh_tokens.add("refresh-1")

    with pytest.raises(Exception, match="invalid_grant"):
        await mail.sync()

    assert mail.checkpoint() is None
    assert mail.mail_ids() == set()


# --- streaming ----------------------------------------------------------------


async def _body(response: StreamingResponse) -> bytes:
    return b"".join([c if isinstance(c, bytes) else c.encode() async for c in response.body_iterator])


async def test_streaming_a_mail_returns_its_html_and_an_attachment_its_bytes(mail: Harness) -> None:
    mail.gmail.deliver(ME, "m1", sender=FRIEND, body="<html><body><p>Quarterly numbers</p></body></html>",
                       attachments=[("q.csv", "text/csv", b"a,b\n1,2\n")])
    await mail.sync()
    connector = await mail.connector_()

    mail_body = await _body(await connector.stream_record(mail.records.records["m1"]))
    attachment = await _body(await connector.stream_record(mail.records.records["m1~1"]))

    assert b"Quarterly numbers" in mail_body
    assert attachment == b"a,b\n1,2\n"


async def test_streaming_a_drive_linked_attachment_downloads_it_from_drive(mail: Harness) -> None:
    mail.drive.add_item("drive-file-1", "deck.pdf", parent="root-me", owner=ME, content=b"%PDF-deck")
    link = "https://drive.google.com/file/d/drive-file-1/view?usp=drive_web"
    mail.gmail.deliver(ME, "m1", sender=FRIEND, body=f"<a href='{link}'>deck</a>")
    await mail.sync()
    connector = await mail.connector_()

    content = await _body(await connector.stream_record(mail.records.records["drive-file-1"]))

    assert content == b"%PDF-deck"


async def test_streaming_mail_deleted_at_source_reports_not_found(mail: Harness) -> None:
    mail.gmail.deliver(ME, "m1", sender=FRIEND)
    await mail.sync()
    mail.gmail.delete(ME, "m1")
    connector = await mail.connector_()

    with pytest.raises(Exception) as raised:
        await connector.stream_record(mail.records.records["m1"])

    assert getattr(raised.value, "status_code", None) == 404


async def test_reindex_republishes_unchanged_mail(mail: Harness) -> None:
    mail.gmail.deliver(ME, "m1", sender=FRIEND)
    await mail.sync()
    connector = await mail.connector_()

    await connector.reindex_records([mail.records.records["m1"]])

    assert [r.external_record_id for r in mail.records.reindexed] == ["m1"]
