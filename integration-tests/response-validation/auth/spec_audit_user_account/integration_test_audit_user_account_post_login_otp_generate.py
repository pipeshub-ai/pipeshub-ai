"""Strict OpenAPI audit of POST /api/v1/userAccount/login/otp/generate."""

from __future__ import annotations

import time
from typing import Any

import pytest
from helper import mailpit
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_account_audit_support import (
    OTP_GENERATE_ROUTE,
    SIGN_IN_CODE_REQUESTED,
    SIGN_IN_CODE_SUBJECT,
    VALIDATION_ERROR,
    Account,
    UserAccountAuditClient,
    lock_account,
)

pytestmark = pytest.mark.spec_audit

ROUTE = OTP_GENERATE_ROUTE
# Long enough for a mail that is going to be sent to reach Mailpit on this stack.
NO_MAIL_WAIT_SECONDS = 3


def _assert_accepted(resp: Any) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.headers["Content-Type"].startswith("text/html")
    assert resp.text == SIGN_IN_CODE_REQUESTED


def test_code_is_emailed_to_an_account(
    user_account_audit_client: UserAccountAuditClient, account: Account, mailbox: None
) -> None:
    seen = mailpit.message_ids(account.email)

    _assert_accepted(user_account_audit_client.otp_generate({"email": account.email}))

    message = mailpit.wait_for_new_message(account.email, SIGN_IN_CODE_SUBJECT, seen)
    code = mailpit.sign_in_code(message)
    assert len(code) == 6 and code.isdigit()


def test_email_with_no_account_gets_the_same_answer_and_no_mail(
    user_account_audit_client: UserAccountAuditClient, stray_email: str, mailbox: None
) -> None:
    _assert_accepted(user_account_audit_client.otp_generate({"email": stray_email}))

    time.sleep(NO_MAIL_WAIT_SECONDS)
    assert mailpit.message_ids(stray_email) == set()


def test_locked_account_gets_the_same_answer_and_no_mail(
    user_account_audit_client: UserAccountAuditClient, account: Account, mailbox: None
) -> None:
    lock_account(account.org_id, account.user_id)

    _assert_accepted(user_account_audit_client.otp_generate({"email": account.email}))

    time.sleep(NO_MAIL_WAIT_SECONDS)
    assert mailpit.message_ids(account.email) == set()


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({}, "body.email", id="no_email"),
        pytest.param(None, "body.email", id="no_body"),
        pytest.param({"email": "not-an-email"}, "body.email", id="malformed_email"),
        pytest.param({"email": ""}, "body.email", id="empty_email"),
        pytest.param({"email": 123}, "body.email", id="numeric_email"),
        pytest.param(
            {"email": "spec-audit@example.com", "cf-turnstile-response": 1},
            "body.cf-turnstile-response",
            id="numeric_turnstile_token",
        ),
        pytest.param(["not", "an", "object"], "body", id="array_body"),
    ],
)
def test_otp_generate_refuses_what_the_validator_refuses(
    user_account_audit_client: UserAccountAuditClient, body: Any, field: str
) -> None:
    resp = user_account_audit_client.otp_generate(body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == VALIDATION_ERROR
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]


def test_turnstile_token_is_accepted_when_captcha_is_off(
    user_account_audit_client: UserAccountAuditClient, stray_email: str
) -> None:
    _assert_accepted(
        user_account_audit_client.otp_generate(
            {"email": stray_email, "cf-turnstile-response": "spec-audit-token"}
        )
    )


def test_unknown_fields_and_the_query_string_are_ignored(
    user_account_audit_client: UserAccountAuditClient, stray_email: str
) -> None:
    with outside_request_contract("the validator drops body fields and query keys it does not know"):
        _assert_accepted(
            user_account_audit_client.otp_generate(
                {"email": stray_email, "specAuditUnknown": True},
                params={"specAuditUnknown": "1"},
            )
        )
