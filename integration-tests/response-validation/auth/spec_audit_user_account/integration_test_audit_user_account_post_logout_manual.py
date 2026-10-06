"""Strict OpenAPI audit of POST /api/v1/userAccount/logout/manual."""

from __future__ import annotations

import time
from typing import Any

import pytest
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_account_audit_support import (
    BAD_REQUEST,
    INTERNAL_ERROR,
    LOGOUT_ROUTE,
    NO_AUTHORIZATION_HEADER,
    Account,
    Tokens,
    UserAccountAuditClient,
)

pytestmark = pytest.mark.spec_audit

ROUTE = LOGOUT_ROUTE
# A sign-out spares tokens issued within the second before it (userActivities.utils.ts).
SIGN_OUT_GRACE_SECONDS = 1.2


def _assert_signed_out(resp: Any) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.content == b""
    assert "Content-Type" not in resp.headers


def test_logout_answers_with_no_body_and_ends_the_refresh_token(
    user_account_audit_client: UserAccountAuditClient, account: Account
) -> None:
    tokens = user_account_audit_client.sign_in(account)
    time.sleep(SIGN_OUT_GRACE_SECONDS)

    _assert_signed_out(user_account_audit_client.logout(token=tokens.access))

    refreshed = user_account_audit_client.refresh_token(token=tokens.refresh)
    assert refreshed.status_code == 401, refreshed.text[:500]
    # This route does not look at earlier sign-outs, so the same access token signs out again.
    _assert_signed_out(user_account_audit_client.logout(token=tokens.access))


def test_body_and_query_string_are_not_read(
    user_account_audit_client: UserAccountAuditClient, account: Account
) -> None:
    tokens = user_account_audit_client.sign_in(account)

    with outside_request_contract("the handler never reads the body or the query string"):
        _assert_signed_out(
            user_account_audit_client.logout(
                {"specAuditUnknown": True}, token=tokens.access, params={"specAuditUnknown": "1"}
            )
        )


@pytest.mark.parametrize(
    ("authorization", "message"),
    [
        pytest.param(None, NO_AUTHORIZATION_HEADER, id="no_authorization_header"),
        pytest.param("Bearer", "Token not found in Authorization header", id="scheme_without_a_token"),
    ],
)
def test_request_without_a_bearer_token_is_bad_request(
    user_account_audit_client: UserAccountAuditClient, authorization: str | None, message: str
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}

    resp = user_account_audit_client.logout(headers=headers)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == BAD_REQUEST
    assert error["message"] == message


@pytest.mark.parametrize("token_kind", ["not_a_jwt", "refresh_token"])
def test_token_that_does_not_verify_is_an_internal_error(
    user_account_audit_client: UserAccountAuditClient, module_tokens: Tokens, token_kind: str
) -> None:
    # jwt.verify throws and nothing maps that to a 401.
    token = "spec-audit-not-a-jwt" if token_kind == "not_a_jwt" else module_tokens.refresh

    resp = user_account_audit_client.logout(token=token)
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == INTERNAL_ERROR
