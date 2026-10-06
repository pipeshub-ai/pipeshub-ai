"""Strict OpenAPI audit of POST /api/v1/userAccount/password/reset/token."""

from __future__ import annotations

from typing import Any

import pytest
from bson import ObjectId
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)
from user_account_audit_support import (
    BAD_REQUEST,
    INTERNAL_ERROR,
    NEW_PASSWORD,
    PASSWORD_RESET_SCOPE,
    PASSWORD_RESET_TOKEN_ROUTE,
    TOKEN_REFRESH_SCOPE,
    UNAUTHORIZED,
    WEAK_PASSWORD,
    WRONG_EMAIL_OR_PASSWORD,
    WRONG_SECRET,
    Account,
    MintUserToken,
    UserAccountAuditClient,
    emailed_reset_token,
    forget_account,
    lock_account,
    mint_scoped_token,
    password_body,
)

pytestmark = pytest.mark.spec_audit

ROUTE = PASSWORD_RESET_TOKEN_ROUTE
RESET = {"data": "password reset"}
PASSWORD_POLICY = (
    "Password should have minimum 8 characters with at least one uppercase, one lowercase, "
    "one number, and one special character, and be no longer than 72 bytes."
)
LINK_EXPIRED = "Password reset link expired, please request for a new link"


def _reset_token(user_token: MintUserToken, account: Account, **overrides: Any) -> str:
    """A token shaped like the one in the emailed link (jwtGeneratorForForgotPasswordLink)."""
    return user_token(
        PASSWORD_RESET_SCOPE,
        account.user_id,
        userEmail=account.email,
        orgId=account.org_id,
        **overrides,
    )


def _assert_refused(resp: Any, status: int, code: str, message: str | None) -> None:
    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == code
    if message is not None:
        assert error["message"] == message


def test_emailed_link_sets_a_new_password_once(
    user_account_audit_client: UserAccountAuditClient, account: Account, mailbox: None
) -> None:
    token = emailed_reset_token(user_account_audit_client, account.email)

    resp = user_account_audit_client.password_reset_token({"password": NEW_PASSWORD}, token=token)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == RESET

    user_account_audit_client.sign_in(account, NEW_PASSWORD)
    old = user_account_audit_client.authenticate(
        password_body(account.password, account.email),
        session=user_account_audit_client.start_session(),
    )
    assert old.status_code == 400, old.text[:500]
    assert old.json()["error"]["message"] == WRONG_EMAIL_OR_PASSWORD

    # The password change is newer than the link, which retires it.
    replay = user_account_audit_client.password_reset_token(
        {"password": "SpecAudit#Pass789"}, token=token
    )
    _assert_refused(replay, 401, UNAUTHORIZED, LINK_EXPIRED)


def test_unknown_fields_and_the_query_string_are_ignored(
    user_account_audit_client: UserAccountAuditClient, account: Account, user_token: MintUserToken
) -> None:
    with outside_request_contract("the route has no validator and reads only password"):
        resp = user_account_audit_client.password_reset_token(
            {"password": NEW_PASSWORD, "specAuditUnknown": True},
            token=_reset_token(user_token, account),
            params={"specAuditUnknown": "1"},
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == RESET


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="no_password"),
        pytest.param(None, id="no_body"),
        pytest.param({"password": ""}, id="empty_password"),
    ],
)
def test_missing_password_is_bad_request(
    user_account_audit_client: UserAccountAuditClient,
    module_account: Account,
    user_token: MintUserToken,
    body: Any,
) -> None:
    resp = user_account_audit_client.password_reset_token(
        body, token=_reset_token(user_token, module_account)
    )
    _assert_refused(resp, 400, BAD_REQUEST, "password is required")
    # The route has no validator; the handler's own check is what makes password required.
    assert_spec_forbids_request(resp, ROUTE)


def test_refused_password_leaves_the_link_usable(
    user_account_audit_client: UserAccountAuditClient, account: Account, user_token: MintUserToken
) -> None:
    token = _reset_token(user_token, account)

    weak = user_account_audit_client.password_reset_token({"password": WEAK_PASSWORD}, token=token)
    _assert_refused(weak, 400, BAD_REQUEST, PASSWORD_POLICY)

    # Eight characters and more, but not the required mix: only the handler can refuse it.
    plain = user_account_audit_client.password_reset_token(
        {"password": "onlylowercaseletters"}, token=token
    )
    _assert_refused(plain, 400, BAD_REQUEST, PASSWORD_POLICY)

    same = user_account_audit_client.password_reset_token(
        {"password": account.password}, token=token
    )
    _assert_refused(same, 400, BAD_REQUEST, "Old and new password cannot be same")

    resp = user_account_audit_client.password_reset_token({"password": NEW_PASSWORD}, token=token)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_locked_account_cannot_reset_its_password(
    user_account_audit_client: UserAccountAuditClient, account: Account, user_token: MintUserToken
) -> None:
    lock_account(account.org_id, account.user_id)

    resp = user_account_audit_client.password_reset_token(
        {"password": NEW_PASSWORD}, token=_reset_token(user_token, account)
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"].startswith(
        "You cannot change you password as your account is blocked due to multiple incorrect logins"
    )


def test_password_that_is_not_a_string_is_an_internal_error(
    user_account_audit_client: UserAccountAuditClient,
    module_account: Account,
    user_token: MintUserToken,
) -> None:
    with outside_request_contract("a number where the handler expects a string; nothing validates the type"):
        resp = user_account_audit_client.password_reset_token(
            {"password": 12345678}, token=_reset_token(user_token, module_account)
        )
        _assert_refused(resp, 500, INTERNAL_ERROR, None)


def test_no_token_is_unauthorized(user_account_audit_client: UserAccountAuditClient) -> None:
    resp = user_account_audit_client.password_reset_token({"password": NEW_PASSWORD})
    _assert_refused(resp, 401, UNAUTHORIZED, "No token provided")


@pytest.mark.parametrize(
    ("case", "message"),
    [
        pytest.param("garbage", "Invalid token", id="not_a_jwt"),
        pytest.param("another_secret", "Invalid token", id="signed_with_another_secret"),
        pytest.param("another_scope", "Invalid scope", id="another_scope"),
        pytest.param("expired", None, id="expired"),
    ],
)
def test_unusable_token_is_unauthorized(
    user_account_audit_client: UserAccountAuditClient,
    module_account: Account,
    user_token: MintUserToken,
    case: str,
    message: str | None,
) -> None:
    token = {
        "garbage": lambda: "spec-audit-not-a-jwt",
        "another_secret": lambda: mint_scoped_token(
            WRONG_SECRET,
            [PASSWORD_RESET_SCOPE],
            userId=module_account.user_id,
            orgId=module_account.org_id,
        ),
        "another_scope": lambda: _reset_token(user_token, module_account, scopes=[TOKEN_REFRESH_SCOPE]),
        "expired": lambda: _reset_token(user_token, module_account, ttl_seconds=-60),
    }[case]()

    resp = user_account_audit_client.password_reset_token({"password": NEW_PASSWORD}, token=token)
    _assert_refused(resp, 401, UNAUTHORIZED, message)


def test_token_naming_no_account_is_an_internal_error(
    user_account_audit_client: UserAccountAuditClient, user_token: MintUserToken
) -> None:
    never_created = str(ObjectId())
    try:
        resp = user_account_audit_client.password_reset_token(
            {"password": NEW_PASSWORD},
            token=user_token(PASSWORD_RESET_SCOPE, never_created, userEmail="spec-audit-nobody@test-pipeshub.com"),
        )
        # The users service answers 404, IamService.getUserById rethrows it as an AxiosError,
        # and that is reported as an unknown error rather than the 404 the handler means to send.
        _assert_refused(resp, 500, INTERNAL_ERROR, None)
    finally:
        # The link is claimed before the account is looked up.
        forget_account(never_created)
