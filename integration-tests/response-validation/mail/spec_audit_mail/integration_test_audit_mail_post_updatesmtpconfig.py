"""Strict OpenAPI audit of POST /api/v1/mail/updateSmtpConfig."""

from __future__ import annotations

import pytest
import requests
from mail_audit_support import (
    FETCH_CONFIG_SCOPE,
    SEND_MAIL_SCOPE,
    SENT_BODY,
    UPDATE_SMTP_CONFIG_ROUTE,
    WRONG_SECRET,
    MailClient,
    MintScopedToken,
    NewRecipient,
    delivered,
    email_body,
    mint_scoped_token,
    request_as,
    scoped_jwt_secret,
)
from helper.second_user import SecondUser
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = [pytest.mark.spec_audit, pytest.mark.usefixtures("smtp_ready")]

ROUTE = UPDATE_SMTP_CONFIG_ROUTE
RELOADED_BODY = {"message": "SMTP configuration updated successfully"}


def _assert_reloaded(resp: requests.Response) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == RELOADED_BODY


def _assert_mail_still_goes_out(
    mail_client: MailClient, scoped_token: MintScopedToken, address: str
) -> None:
    sent = mail_client.send_email(
        email_body(to=[address]), token=scoped_token(SEND_MAIL_SCOPE)
    )
    assert sent.status_code == 200, sent.text[:500]
    assert sent.json() == SENT_BODY
    assert len(delivered(address)) == 1


def test_update_smtp_config_reloads_stored_config(
    mail_client: MailClient, scoped_token: MintScopedToken, new_recipient: NewRecipient
) -> None:
    resp = mail_client.update_smtp_config(token=scoped_token(FETCH_CONFIG_SCOPE))

    _assert_reloaded(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    _assert_mail_still_goes_out(mail_client, scoped_token, new_recipient())


def test_update_smtp_config_does_not_read_the_request(
    mail_client: MailClient, scoped_token: MintScopedToken, new_recipient: NewRecipient
) -> None:
    # A body shaped like an SMTP config for a server that does not exist: were it
    # read, the send below would fail.
    unreachable = {
        "host": "smtp.spec-audit.invalid",
        "port": 2525,
        "fromEmail": "nobody@spec-audit.invalid",
    }
    with outside_request_contract("the handler never reads the body or the query string"):
        resp = mail_client.update_smtp_config(
            unreachable,
            token=scoped_token(FETCH_CONFIG_SCOPE),
            params={"host": "smtp.spec-audit.invalid"},
        )

    _assert_reloaded(resp)
    assert_strict_openapi_response(resp, ROUTE)
    _assert_mail_still_goes_out(mail_client, scoped_token, new_recipient())


def test_update_smtp_config_rejects_session_tokens(
    mail_client: MailClient, second_user: SecondUser
) -> None:
    # Session tokens are signed with another key, so admin and member alike
    # fail verification (401) instead of reaching a role check (403).
    admin_resp = mail_client.update_smtp_config()
    assert admin_resp.status_code == 401, admin_resp.text[:500]
    assert admin_resp.json()["error"]["message"] == "Invalid token"
    assert_strict_openapi_exchange(admin_resp, ROUTE)

    member_resp = request_as(second_user, "POST", "/updateSmtpConfig")
    assert member_resp.status_code == 401, member_resp.text[:500]
    assert member_resp.json()["error"]["message"] == "Invalid token"
    assert_strict_openapi_exchange(member_resp, ROUTE)


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("no_token", "No token provided"),
        ("basic_scheme", "No token provided"),
        ("wrong_secret", "Invalid token"),
        ("expired", "Invalid token"),
        ("other_mail_scope", "Invalid scope"),
        ("no_scopes_claim", "Invalid scope"),
    ],
)
def test_update_smtp_config_without_a_fetch_config_token_is_unauthorized(
    mail_client: MailClient, scoped_token: MintScopedToken, kind: str, message: str
) -> None:
    if kind == "no_token":
        resp = mail_client.update_smtp_config(auth=False)
    elif kind == "basic_scheme":
        resp = mail_client.update_smtp_config(
            auth=False, headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
    else:
        token = {
            "wrong_secret": lambda: mint_scoped_token(WRONG_SECRET, [FETCH_CONFIG_SCOPE]),
            "expired": lambda: scoped_token(FETCH_CONFIG_SCOPE, ttl_seconds=-60),
            "other_mail_scope": lambda: scoped_token(SEND_MAIL_SCOPE),
            "no_scopes_claim": lambda: mint_scoped_token(scoped_jwt_secret(), None),
        }[kind]()
        resp = mail_client.update_smtp_config(token=token)

    assert resp.status_code == 401, resp.text[:500]
    assert resp.json()["error"]["message"] == message
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_smtp_config_accepts_a_string_scopes_claim_that_contains_the_scope(
    mail_client: MailClient,
) -> None:
    # API bug: the check is scopes.includes(scope), which on a string is a substring test,
    # so this token passes although it holds no "fetch:config" scope.
    token = mint_scoped_token(scoped_jwt_secret(), f"not-{FETCH_CONFIG_SCOPE}-at-all")

    resp = mail_client.update_smtp_config(token=token)

    _assert_reloaded(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
