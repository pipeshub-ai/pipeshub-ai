"""Strict OpenAPI audit of POST /api/v1/mail/emails/sendEmail.

No case sends a real template type: a 200 here means an email was delivered.
"""

from __future__ import annotations

import pytest
import requests
from mail_audit_support import (
    FETCH_CONFIG_SCOPE,
    SEND_MAIL_SCOPE,
    WRONG_SECRET,
    MailClient,
    MintScopedToken,
    mint_scoped_token,
    smtp_is_configured,
    unknown_template_body,
)
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mail/emails/sendEmail"

SMTP_NOT_CONFIGURED = "Smtp not configured properly"


def _assert_refused(
    resp: requests.Response,
    pipeshub_client: PipeshubClient,
    status: int,
    message: str,
) -> None:
    # smtpConfigChecker runs before the token check, so an org without SMTP
    # answers 400 to every request, whatever its token or body.
    if not smtp_is_configured(pipeshub_client):
        status, message = 400, SMTP_NOT_CONFIGURED
    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert message in resp.text, resp.text[:500]


def test_send_email_unknown_template_fails_before_smtp(
    mail_client: MailClient,
    scoped_token: MintScopedToken,
    pipeshub_client: PipeshubClient,
) -> None:
    resp = mail_client.send_email(
        unknown_template_body(pipeshub_client.org_id),
        token=scoped_token(SEND_MAIL_SCOPE),
    )
    # Rendering fails, the sender reports status=false, the controller throws 500.
    _assert_refused(resp, pipeshub_client, 500, "")


def test_send_email_without_token_is_unauthorized(
    mail_client: MailClient, pipeshub_client: PipeshubClient
) -> None:
    resp = mail_client.send_email(unknown_template_body(), auth=False)
    _assert_refused(resp, pipeshub_client, 401, "No token provided")


@pytest.mark.parametrize("token_kind", ["admin_session", "wrong_secret"])
def test_send_email_token_not_signed_with_scoped_secret_is_unauthorized(
    mail_client: MailClient, pipeshub_client: PipeshubClient, token_kind: str
) -> None:
    if token_kind == "admin_session":
        resp = mail_client.send_email(unknown_template_body(), auth=True)
    else:
        token = mint_scoped_token(WRONG_SECRET, [SEND_MAIL_SCOPE])
        resp = mail_client.send_email(unknown_template_body(), token=token)
    _assert_refused(resp, pipeshub_client, 401, "Invalid token")


def test_send_email_token_with_other_scope_is_unauthorized(
    mail_client: MailClient,
    scoped_token: MintScopedToken,
    pipeshub_client: PipeshubClient,
) -> None:
    resp = mail_client.send_email(
        unknown_template_body(), token=scoped_token(FETCH_CONFIG_SCOPE)
    )
    _assert_refused(resp, pipeshub_client, 401, "Invalid scope")
