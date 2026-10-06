"""Strict OpenAPI audit of POST /api/v1/userAccount/password/forgot."""

from __future__ import annotations

import time
from typing import Any

import pytest
from helper import mailpit
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)
from user_account_audit_support import (
    BAD_REQUEST,
    PASSWORD_FORGOT_ROUTE,
    RESET_LINK_SUBJECT,
    Account,
    UserAccountAuditClient,
)

pytestmark = pytest.mark.spec_audit

ROUTE = PASSWORD_FORGOT_ROUTE
ACCEPTED = {"data": "If an account exists for this email, a password reset link has been sent."}
NO_MAIL_WAIT_SECONDS = 3


def _assert_accepted(resp: Any) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == ACCEPTED


def test_reset_link_is_emailed_to_an_account(
    user_account_audit_client: UserAccountAuditClient, account: Account, mailbox: None
) -> None:
    seen = mailpit.message_ids(account.email)

    _assert_accepted(user_account_audit_client.password_forgot({"email": account.email}))

    message = mailpit.wait_for_new_message(account.email, RESET_LINK_SUBJECT, seen)
    assert mailpit.reset_link_token(message).count(".") == 2


def test_email_with_no_account_gets_the_same_answer_and_no_mail(
    user_account_audit_client: UserAccountAuditClient, stray_email: str, mailbox: None
) -> None:
    _assert_accepted(user_account_audit_client.password_forgot({"email": stray_email}))

    time.sleep(NO_MAIL_WAIT_SECONDS)
    assert mailpit.message_ids(stray_email) == set()


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="no_email"),
        pytest.param(None, id="no_body"),
        pytest.param({"email": ""}, id="empty_email"),
        pytest.param(["not", "an", "object"], id="array_body"),
    ],
)
def test_missing_email_is_bad_request(
    user_account_audit_client: UserAccountAuditClient, body: Any
) -> None:
    resp = user_account_audit_client.password_forgot(body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == BAD_REQUEST
    assert error["message"] == "Email is required"
    # The route has no validator; the handler's own check is what makes email required.
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    "email",
    [
        pytest.param("not-an-email", id="malformed_email"),
        pytest.param(123, id="numeric_email"),
    ],
)
def test_email_is_not_validated(
    user_account_audit_client: UserAccountAuditClient, email: Any
) -> None:
    # The account lookup fails on these and the failure is swallowed, like an unknown email.
    with outside_request_contract("the route has no validator: any truthy email gets the generic answer"):
        _assert_accepted(user_account_audit_client.password_forgot({"email": email}))


def test_turnstile_token_is_accepted_when_captcha_is_off(
    user_account_audit_client: UserAccountAuditClient, stray_email: str
) -> None:
    _assert_accepted(
        user_account_audit_client.password_forgot(
            {"email": stray_email, "cf-turnstile-response": "spec-audit-token"}
        )
    )


def test_unknown_fields_and_the_query_string_are_ignored(
    user_account_audit_client: UserAccountAuditClient, stray_email: str
) -> None:
    with outside_request_contract("the route has no validator and reads only email and the CAPTCHA token"):
        _assert_accepted(
            user_account_audit_client.password_forgot(
                {"email": stray_email, "specAuditUnknown": True},
                params={"specAuditUnknown": "1"},
            )
        )
