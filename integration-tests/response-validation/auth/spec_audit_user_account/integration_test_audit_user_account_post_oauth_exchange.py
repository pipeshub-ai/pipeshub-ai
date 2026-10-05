"""Strict OpenAPI audit of POST /api/v1/userAccount/oauth/exchange."""

from __future__ import annotations

import pytest
from strict_openapi import assert_strict_openapi_response
from user_account_audit_support import UserAccountAuditClient, oauth_exchange_body

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/userAccount/oauth/exchange"

# OAUTH_SIGN_IN_FAILED in backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts.
OAUTH_SIGN_IN_FAILED = (
    "Sign-in with your identity provider didn't complete. Try again; if it keeps "
    "happening, ask your admin to check the sign-in settings."
)


@pytest.mark.parametrize("missing_field", ["code", "provider", "redirectUri"])
def test_missing_required_field_is_bad_request(
    user_account_audit_client: UserAccountAuditClient, missing_field: str
) -> None:
    body = oauth_exchange_body(**{missing_field: None})

    resp = user_account_audit_client.oauth_exchange(body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_BAD_REQUEST"
    assert error["message"] == OAUTH_SIGN_IN_FAILED


def test_fake_code_is_bad_request_without_a_session(
    user_account_audit_client: UserAccountAuditClient,
) -> None:
    # 400 on both branches: no OAuth sign-in configured for the org, or the
    # configured provider rejecting a code it never issued.
    resp = user_account_audit_client.oauth_exchange(oauth_exchange_body())
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST"


@pytest.mark.xfail(
    strict=True,
    reason="API bug: malformed JSON body answers 500 INTERNAL_ERROR instead of 400",
)
def test_malformed_json_body_is_bad_request(
    user_account_audit_client: UserAccountAuditClient,
) -> None:
    # express.json() raises a SyntaxError (status 400), which is not a BaseError,
    # so the error middleware answers 500 rather than body-parser's own 400.
    resp = user_account_audit_client.oauth_exchange(
        data='{"code": "spec-audit", "provider": ',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST"
