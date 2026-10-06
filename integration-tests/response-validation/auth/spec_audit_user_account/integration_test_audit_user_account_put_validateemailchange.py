"""Strict OpenAPI audit of PUT /api/v1/userAccount/validateEmailChange."""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from bson import ObjectId
from helper.clients.users_client import UsersClient
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from user_account_audit_support import (
    INTERNAL_ERROR,
    MALFORMED_USER_ID,
    MISSING_USER_ID,
    OTHER_SCOPE,
    UNAUTHORIZED,
    VALIDATE_EMAIL_SCOPE,
    VALIDATE_EMAIL_CHANGE_ROUTE,
    WRONG_SECRET,
    DisposableMember,
    MintEmailChangeToken,
    MintUserToken,
    UserAccountAuditClient,
    forget_account,
    mint_scoped_token,
    unused_email,
)

pytestmark = pytest.mark.spec_audit

ROUTE = VALIDATE_EMAIL_CHANGE_ROUTE
UPDATED = {"message": "Email updated successfully"}
# A link is retired by an activity newer than its iat, which has one-second granularity.
IAT_GRANULARITY_SECONDS = 1.2


def _assert_updated(resp: Any) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == UPDATED


def _assert_refused(resp: Any, status: int, code: str, message: str | None = None) -> None:
    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == code
    if message is not None:
        assert error["message"] == message


def _stored_email(users_client: UsersClient, user_id: str) -> str:
    stored = users_client.get_user_email(user_id)
    assert stored.status_code == 200, stored.text[:500]
    return str(stored.json()["email"])


def test_valid_token_changes_email_and_is_single_use(
    user_account_audit_client: UserAccountAuditClient,
    email_change_token: MintEmailChangeToken,
    disposable_member: DisposableMember,
    users_client: UsersClient,
) -> None:
    new_email = unused_email("spec-audit-changed")
    token = email_change_token(disposable_member.user_id, new_email)

    _assert_updated(user_account_audit_client.validate_email_change(token=token))
    assert _stored_email(users_client, disposable_member.user_id) == new_email

    # The 200 records an activity newer than the token's iat, which retires the link.
    replay = user_account_audit_client.validate_email_change(token=token)
    _assert_refused(replay, 401, UNAUTHORIZED)
    assert "link expired" in replay.json()["error"]["message"]


def test_new_email_is_stored_lower_cased_and_not_validated(
    user_account_audit_client: UserAccountAuditClient,
    email_change_token: MintEmailChangeToken,
    disposable_member: DisposableMember,
    users_client: UsersClient,
) -> None:
    raw = f"  Spec-Audit-Not-An-Email-{uuid.uuid4().hex[:8]} "
    _assert_updated(
        user_account_audit_client.validate_email_change(
            token=email_change_token(disposable_member.user_id, raw)
        )
    )
    assert _stored_email(users_client, disposable_member.user_id) == raw.strip().lower()


def test_token_without_new_email_stores_the_word_undefined(
    user_account_audit_client: UserAccountAuditClient,
    user_token: MintUserToken,
    email_change_token: MintEmailChangeToken,
    disposable_member: DisposableMember,
    users_client: UsersClient,
) -> None:
    resp = user_account_audit_client.validate_email_change(
        token=user_token(VALIDATE_EMAIL_SCOPE, disposable_member.user_id)
    )
    try:
        _assert_updated(resp)
        assert _stored_email(users_client, disposable_member.user_id) == "undefined"
    finally:
        # Put a unique address back, or the next run would find "undefined" taken.
        time.sleep(IAT_GRANULARITY_SECONDS)
        restored = user_account_audit_client.validate_email_change(
            token=email_change_token(disposable_member.user_id, disposable_member.email)
        )
        assert restored.status_code == 200, restored.text[:500]


def test_token_naming_no_account_still_reports_success(
    user_account_audit_client: UserAccountAuditClient, email_change_token: MintEmailChangeToken
) -> None:
    never_created = str(ObjectId())
    try:
        _assert_updated(
            user_account_audit_client.validate_email_change(
                token=email_change_token(never_created, unused_email())
            )
        )
    finally:
        # The activity row is written whether or not an account was updated.
        forget_account(never_created)


def test_token_whose_user_id_is_not_an_object_id_is_an_internal_error(
    user_account_audit_client: UserAccountAuditClient, email_change_token: MintEmailChangeToken
) -> None:
    resp = user_account_audit_client.validate_email_change(
        token=email_change_token(MALFORMED_USER_ID, unused_email())
    )
    _assert_refused(resp, 500, INTERNAL_ERROR)


def test_body_and_query_string_are_not_read(
    user_account_audit_client: UserAccountAuditClient,
    email_change_token: MintEmailChangeToken,
    disposable_member: DisposableMember,
    users_client: UsersClient,
) -> None:
    new_email = unused_email("spec-audit-changed")
    with outside_request_contract("the handler reads only the token's claims"):
        resp = user_account_audit_client.validate_email_change(
            {"newEmail": "spec-audit-ignored@test-pipeshub.com", "specAuditUnknown": True},
            token=email_change_token(disposable_member.user_id, new_email),
            params={"specAuditUnknown": "1"},
        )
        _assert_updated(resp)
    assert _stored_email(users_client, disposable_member.user_id) == new_email


def test_email_owned_by_another_account_is_bad_request(
    user_account_audit_client: UserAccountAuditClient,
    email_change_token: MintEmailChangeToken,
    disposable_member: DisposableMember,
    users_client: UsersClient,
) -> None:
    # The uniqueness check runs before any lookup of the token's own user, so an
    # id that matches nobody reaches it and no account can be modified. A fresh id:
    # a stale activity row for a fixed one could retire the token first (401).
    token = email_change_token(str(ObjectId()), disposable_member.email)

    resp = user_account_audit_client.validate_email_change(token=token)
    _assert_refused(resp, 400, "HTTP_BAD_REQUEST", f"Email already in use: {disposable_member.email}")
    assert _stored_email(users_client, disposable_member.user_id) == disposable_member.email


@pytest.mark.parametrize(
    ("auth", "message"),
    [
        pytest.param(False, "No token provided", id="no_authorization_header"),
        # A session token is signed with the session secret and carries no scopes,
        # so it fails on signature or on scope depending on how the secrets are set.
        pytest.param(True, None, id="admin_session_token"),
    ],
)
def test_request_without_scoped_token_is_unauthorized(
    user_account_audit_client: UserAccountAuditClient,
    auth: bool,
    message: str | None,
) -> None:
    resp = user_account_audit_client.validate_email_change(auth=auth)
    _assert_refused(resp, 401, UNAUTHORIZED, message)
    if message is None:
        assert resp.json()["error"]["message"] in ("Invalid token", "Invalid scope")


@pytest.mark.parametrize(
    ("case", "message"),
    [
        pytest.param("another_scope", "Invalid scope", id="another_scope"),
        pytest.param("another_secret", "Invalid token", id="signed_with_another_secret"),
        pytest.param("not_a_jwt", "Invalid token", id="not_a_jwt"),
        pytest.param("expired", None, id="expired"),
    ],
)
def test_unusable_token_is_unauthorized(
    user_account_audit_client: UserAccountAuditClient,
    email_change_token: MintEmailChangeToken,
    case: str,
    message: str | None,
) -> None:
    token = {
        "another_scope": lambda: email_change_token(MISSING_USER_ID, unused_email(), scopes=[OTHER_SCOPE]),
        "another_secret": lambda: mint_scoped_token(
            WRONG_SECRET, [VALIDATE_EMAIL_SCOPE], userId=MISSING_USER_ID, newEmail=unused_email()
        ),
        "not_a_jwt": lambda: "spec-audit-not-a-jwt",
        "expired": lambda: email_change_token(MISSING_USER_ID, unused_email(), ttl_seconds=-60),
    }[case]()

    _assert_refused(user_account_audit_client.validate_email_change(token=token), 401, UNAUTHORIZED, message)
