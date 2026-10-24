"""What a Gmail or Google sign-in failure means (gmail_errors.classify)."""

import pytest
from _gmail_errors_fixtures import http_error
from google.auth.exceptions import RefreshError

from app.connectors.sources.google.common.gmail_errors import (
    GmailFailure,
    classify,
    retry_after_seconds,
)


@pytest.mark.parametrize(("error", "failure"), [
    (http_error(429, "rateLimitExceeded"), GmailFailure.RATE_LIMITED),
    (http_error(403, "userRateLimitExceeded"), GmailFailure.RATE_LIMITED),
    (http_error(403, "rateLimitExceeded"), GmailFailure.RATE_LIMITED),
    (http_error(503, "backendError"), GmailFailure.TRANSIENT),
    (http_error(500), GmailFailure.TRANSIENT),
    (http_error(404, "notFound"), GmailFailure.NOT_FOUND),
    (http_error(400, "failedPrecondition", "Mail service not enabled"), GmailFailure.MAIL_DISABLED),
    (http_error(403, "insufficientPermissions"), GmailFailure.MISSING_SCOPE),
    (http_error(401, "authError"), GmailFailure.RECONNECT),
    (http_error(403, "forbidden", "Delegation denied"), GmailFailure.OTHER),
    (RefreshError("unauthorized_client: Client is unauthorized to retrieve access tokens"),
     GmailFailure.DELEGATION_REFUSED),
    (RefreshError("invalid_grant: Invalid JWT Signature."), GmailFailure.KEY_REJECTED),
    (RefreshError("invalid_grant: Invalid email or User ID"), GmailFailure.USER_UNAVAILABLE),
    (RefreshError("invalid_grant: Token has been expired or revoked."), GmailFailure.RECONNECT),
    (ConnectionResetError("reset"), GmailFailure.TRANSIENT),
    (TimeoutError("timed out"), GmailFailure.TRANSIENT),
    (ValueError("something else"), GmailFailure.OTHER),
])
def test_classify(error: BaseException, failure: GmailFailure) -> None:
    assert classify(error) is failure


def test_classify_reads_the_cause_of_a_wrapped_error() -> None:
    try:
        try:
            raise RefreshError("unauthorized_client: nope")
        except RefreshError as inner:
            raise RuntimeError("Failed to get enterprise token") from inner
    except RuntimeError as outer:
        assert classify(outer) is GmailFailure.DELEGATION_REFUSED


def test_retry_after_reads_the_time_gmail_names() -> None:
    error = http_error(429, "rateLimitExceeded", "User-rate limit exceeded.  Retry after 2026-10-23T10:00:30.000Z")
    now = 1792749600.0  # 2026-10-23T10:00:00Z
    assert retry_after_seconds(error, now) == pytest.approx(30.0)


def test_retry_after_without_a_time() -> None:
    assert retry_after_seconds(http_error(429, "rateLimitExceeded"), 0.0) is None
