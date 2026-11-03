"""GmailSyncHealth decides a run's outcome; GmailNotices what a person is told."""

import pytest
from _gmail_errors_fixtures import http_error
from google.auth.exceptions import RefreshError

from app.connectors.sources.google.common.gmail_errors import GmailFailure
from app.connectors.sources.google.common.gmail_notices import GmailNotices
from app.connectors.sources.google.common.gmail_sync_health import (
    GmailSyncHealth,
    GmailSyncIncomplete,
)

INTERNALS = ("http", "googleapis", "invalid_grant", "unauthorized_client", "Error", "{", "403", "401", "@")


def test_a_failed_thread_makes_the_run_incomplete() -> None:
    health = GmailSyncHealth()
    health.record("a@x.example", http_error(503), "thread t1")
    assert not health.complete
    assert not health.coverage().complete
    with pytest.raises(GmailSyncIncomplete):
        health.raise_if_incomplete()


def test_gmail_turned_off_is_not_a_failure() -> None:
    health = GmailSyncHealth()
    health.record("a@x.example", http_error(400, "failedPrecondition", "Mail service not enabled"), "mailbox")
    assert health.complete
    health.raise_if_incomplete()
    assert health.mailboxes_skipped(GmailFailure.MAIL_DISABLED) == {"a@x.example"}


class Sent:
    def __init__(self) -> None:
        self.notices: list[dict] = []

    async def __call__(self, **kwargs) -> None:
        self.notices.append(kwargs)


def notices(workspace: bool) -> tuple[GmailNotices, Sent]:
    sent = Sent()
    return GmailNotices(sent, lambda event: f"Gmail (test): {event}", workspace=workspace), sent


@pytest.mark.asyncio
async def test_a_passing_outage_sends_nothing() -> None:
    health = GmailSyncHealth()
    health.record("a@x.example", http_error(503), "mailbox")
    gmail, sent = notices(workspace=True)
    await gmail.report(health)
    assert sent.notices == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "workspace", "words"), [
    (RefreshError("unauthorized_client: x"), True, "domain-wide delegation"),
    (RefreshError("invalid_grant: Invalid JWT Signature."), True, "service account key"),
    (RefreshError("invalid_grant: Token has been expired or revoked."), False, "sign in to Google again"),
    (RefreshError("invalid_grant: Token has been expired or revoked."), True, "service account key"),
    (RefreshError("access_denied"), False, "sign in to Google again"),
    (http_error(403, "insufficientPermissions"), False, "sign in to Google again"),
    (http_error(403, "insufficientPermissions"), True, "domain-wide delegation"),
])
async def test_problems_a_person_can_fix_are_sent_once_in_plain_words(error, workspace, words) -> None:
    health = GmailSyncHealth()
    health.record("a@x.example", error, "mailbox")
    health.record("b@x.example", error, "mailbox")
    gmail, sent = notices(workspace=workspace)
    await gmail.report(health)
    assert len(sent.notices) == 1
    text = sent.notices[0]["title"] + " " + sent.notices[0]["message"]
    assert words in text
    assert not [w for w in INTERNALS if w in text], text


@pytest.mark.asyncio
async def test_a_personal_rejected_client_is_not_told_to_upload_a_service_account_key() -> None:
    health = GmailSyncHealth()
    health.record("a@x.example", RefreshError("invalid_client"), "mailbox")
    gmail, sent = notices(workspace=False)
    await gmail.report(health)
    assert sent.notices == []


@pytest.mark.asyncio
async def test_gmail_turned_off_is_counted_not_named() -> None:
    health = GmailSyncHealth()
    for person in ("a@x.example", "b@x.example"):
        health.record(person, http_error(400, "failedPrecondition", "Mail service not enabled"), "mailbox")
    gmail, sent = notices(workspace=True)
    await gmail.report(health)
    assert len(sent.notices) == 1 and "2 people" in sent.notices[0]["message"]
