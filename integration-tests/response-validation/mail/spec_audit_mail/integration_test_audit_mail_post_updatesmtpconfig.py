"""Strict OpenAPI audit of POST /api/v1/mail/updateSmtpConfig."""

from __future__ import annotations

import pytest
from mail_audit_support import (
    FETCH_CONFIG_SCOPE,
    SEND_MAIL_SCOPE,
    WRONG_SECRET,
    MailClient,
    MintScopedToken,
    mint_scoped_token,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mail/updateSmtpConfig"


def test_update_smtp_config_reloads_stored_config(
    mail_client: MailClient, scoped_token: MintScopedToken
) -> None:
    # The body is never read: the route only reloads the stored config into
    # the mail container, so an arbitrary body must not turn into a 400.
    resp = mail_client.update_smtp_config(
        {"host": "", "port": "not-a-port"}, token=scoped_token(FETCH_CONFIG_SCOPE)
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"message": "SMTP configuration updated successfully"}


def test_update_smtp_config_without_token_is_unauthorized(
    mail_client: MailClient,
) -> None:
    resp = mail_client.update_smtp_config(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "No token provided" in resp.text


def test_update_smtp_config_rejects_session_tokens(
    mail_client: MailClient, second_user: SecondUser
) -> None:
    # Session tokens are signed with another key, so admin and member alike
    # fail verification (401) instead of reaching a role check (403).
    admin_resp = mail_client.update_smtp_config()
    assert admin_resp.status_code == 401, admin_resp.text[:500]
    assert_strict_openapi_response(admin_resp, ROUTE)

    member_resp = request_as(second_user, "POST", "/updateSmtpConfig")
    assert member_resp.status_code == 401, member_resp.text[:500]
    assert_strict_openapi_response(member_resp, ROUTE)


def test_update_smtp_config_rejects_wrongly_signed_token(
    mail_client: MailClient,
) -> None:
    token = mint_scoped_token(WRONG_SECRET, [FETCH_CONFIG_SCOPE])
    resp = mail_client.update_smtp_config(token=token)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "Invalid token" in resp.text


def test_update_smtp_config_rejects_other_mail_scope(
    mail_client: MailClient, scoped_token: MintScopedToken
) -> None:
    resp = mail_client.update_smtp_config(token=scoped_token(SEND_MAIL_SCOPE))
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "Invalid scope" in resp.text
