"""Every /api/v1/userAccount route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, before any session or token is looked at.
"""

from __future__ import annotations

import pytest
from strict_openapi import assert_strict_openapi_exchange
from user_account_audit_support import (
    AUTHENTICATE_ROUTE,
    INIT_AUTH_ROUTE,
    INTERNAL_ERROR,
    JSON_HEADERS,
    LOGOUT_ROUTE,
    MALFORMED_JSON_BODY,
    OAUTH_EXCHANGE_ROUTE,
    OTP_GENERATE_ROUTE,
    PASSWORD_FORGOT_ROUTE,
    PASSWORD_RESET_ROUTE,
    PASSWORD_RESET_TOKEN_ROUTE,
    REFRESH_TOKEN_ROUTE,
    VALIDATE_EMAIL_CHANGE_ROUTE,
    UserAccountAuditClient,
)

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("POST", INIT_AUTH_ROUTE),
        ("POST", AUTHENTICATE_ROUTE),
        ("POST", OTP_GENERATE_ROUTE),
        ("POST", PASSWORD_RESET_ROUTE),
        ("POST", REFRESH_TOKEN_ROUTE),
        ("POST", LOGOUT_ROUTE),
        ("POST", PASSWORD_RESET_TOKEN_ROUTE),
        ("POST", PASSWORD_FORGOT_ROUTE),
        ("POST", OAUTH_EXCHANGE_ROUTE),
        ("PUT", VALIDATE_EMAIL_CHANGE_ROUTE),
    ],
    ids=[
        "init_auth",
        "authenticate",
        "otp_generate",
        "password_reset",
        "refresh_token",
        "logout",
        "password_reset_token",
        "password_forgot",
        "oauth_exchange",
        "validate_email_change",
    ],
)
def test_malformed_json_body_is_an_internal_error(
    user_account_audit_client: UserAccountAuditClient, method: str, route: str
) -> None:
    resp = user_account_audit_client._call(
        method,
        route.removeprefix(UserAccountAuditClient.BASE),
        data=MALFORMED_JSON_BODY,
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == INTERNAL_ERROR, resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
