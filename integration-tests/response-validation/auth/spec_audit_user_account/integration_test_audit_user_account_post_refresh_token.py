"""Strict OpenAPI audit of POST /api/v1/userAccount/refresh/token."""

from __future__ import annotations

import time
from typing import Any

import pytest
from bson import ObjectId
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_account_audit_support import (
    ACCOUNT_NO_LONGER_ACTIVE,
    BAD_REQUEST,
    PASSWORD_RESET_SCOPE,
    REFRESH_TOKEN_ROUTE,
    TOKEN_REFRESH_SCOPE,
    UNAUTHORIZED,
    WRONG_SECRET,
    Account,
    MintUserToken,
    Tokens,
    UserAccountAuditClient,
    credentials_rows,
    delete_credentials,
    forget_account,
    lock_account,
    mint_scoped_token,
)

pytestmark = pytest.mark.spec_audit

ROUTE = REFRESH_TOKEN_ROUTE
SESSION_EXPIRED = "Session expired, please login again"
# A sign-out spares tokens issued within the second before it (userActivities.utils.ts).
SIGN_OUT_GRACE_SECONDS = 1.2


def _assert_refused(resp: Any, status: int, code: str, message: str | None) -> None:
    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == code
    if message is not None:
        assert error["message"] == message
    assert "accessToken" not in resp.text


def test_refresh_returns_the_user_and_a_new_access_token(
    user_account_audit_client: UserAccountAuditClient, module_account: Account, module_tokens: Tokens
) -> None:
    resp = user_account_audit_client.refresh_token(token=module_tokens.refresh)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    answer = resp.json()
    assert answer["user"]["_id"] == module_account.user_id
    assert answer["user"]["email"] == module_account.email
    assert answer["accessToken"].count(".") == 2


def test_body_and_query_string_are_not_read(
    user_account_audit_client: UserAccountAuditClient, module_tokens: Tokens
) -> None:
    with outside_request_contract("the handler never reads the body or the query string"):
        resp = user_account_audit_client.refresh_token(
            {"specAuditUnknown": True},
            token=module_tokens.refresh,
            params={"specAuditUnknown": "1"},
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_missing_credentials_row_is_created_not_reported(
    user_account_audit_client: UserAccountAuditClient, account: Account
) -> None:
    tokens = user_account_audit_client.sign_in(account)
    delete_credentials(account.org_id, account.user_id)

    resp = user_account_audit_client.refresh_token(token=tokens.refresh)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert credentials_rows(account.org_id, account.user_id) == 1


def test_locked_account_is_bad_request(
    user_account_audit_client: UserAccountAuditClient, account: Account
) -> None:
    tokens = user_account_audit_client.sign_in(account)
    lock_account(account.org_id, account.user_id)

    resp = user_account_audit_client.refresh_token(token=tokens.refresh)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == BAD_REQUEST
    assert error["message"].startswith("Your account has been disabled.")


def test_no_token_is_unauthorized(user_account_audit_client: UserAccountAuditClient) -> None:
    _assert_refused(user_account_audit_client.refresh_token(), 401, UNAUTHORIZED, "No token provided")


@pytest.mark.parametrize(
    ("case", "message"),
    [
        pytest.param("garbage", "Invalid token", id="not_a_jwt"),
        pytest.param("access_token", "Invalid token", id="access_token"),
        pytest.param("another_secret", "Invalid token", id="signed_with_another_secret"),
        pytest.param("another_scope", "Invalid scope", id="another_scope"),
        pytest.param("expired", None, id="expired"),
    ],
)
def test_unusable_token_is_unauthorized(
    user_account_audit_client: UserAccountAuditClient,
    module_account: Account,
    module_tokens: Tokens,
    user_token: MintUserToken,
    case: str,
    message: str | None,
) -> None:
    token = {
        "garbage": lambda: "spec-audit-not-a-jwt",
        "access_token": lambda: module_tokens.access,
        "another_secret": lambda: mint_scoped_token(
            WRONG_SECRET,
            [TOKEN_REFRESH_SCOPE],
            userId=module_account.user_id,
            orgId=module_account.org_id,
        ),
        "another_scope": lambda: user_token(PASSWORD_RESET_SCOPE, module_account.user_id),
        "expired": lambda: user_token(TOKEN_REFRESH_SCOPE, module_account.user_id, ttl_seconds=-60),
    }[case]()

    _assert_refused(user_account_audit_client.refresh_token(token=token), 401, UNAUTHORIZED, message)


def test_token_issued_before_a_sign_out_is_unauthorized(
    user_account_audit_client: UserAccountAuditClient, account: Account
) -> None:
    tokens = user_account_audit_client.sign_in(account)
    time.sleep(SIGN_OUT_GRACE_SECONDS)
    signed_out = user_account_audit_client.logout(token=tokens.access)
    assert signed_out.status_code == 200, signed_out.text[:500]

    _assert_refused(
        user_account_audit_client.refresh_token(token=tokens.refresh), 401, UNAUTHORIZED, SESSION_EXPIRED
    )


def test_token_of_a_deleted_account_is_unauthorized(
    user_account_audit_client: UserAccountAuditClient,
    pipeshub_client: PipeshubClient,
    account: Account,
) -> None:
    tokens = user_account_audit_client.sign_in(account)
    deleted = pipeshub_client.request("DELETE", f"/api/v1/users/{account.user_id}")
    assert deleted.status_code == 200, deleted.text[:500]

    _assert_refused(
        user_account_audit_client.refresh_token(token=tokens.refresh),
        401,
        UNAUTHORIZED,
        ACCOUNT_NO_LONGER_ACTIVE,
    )


def test_token_naming_no_account_is_unauthorized(
    user_account_audit_client: UserAccountAuditClient, user_token: MintUserToken
) -> None:
    never_created = str(ObjectId())
    try:
        resp = user_account_audit_client.refresh_token(
            token=user_token(TOKEN_REFRESH_SCOPE, never_created)
        )
        _assert_refused(resp, 401, UNAUTHORIZED, ACCOUNT_NO_LONGER_ACTIVE)
    finally:
        # The refresh is logged before the account is looked up.
        forget_account(never_created)
