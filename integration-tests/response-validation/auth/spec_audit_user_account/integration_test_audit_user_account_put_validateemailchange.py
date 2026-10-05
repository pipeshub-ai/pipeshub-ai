"""Strict OpenAPI audit of PUT /api/v1/userAccount/validateEmailChange."""

from __future__ import annotations

import uuid

import pytest
from helper.clients.users_client import UsersClient
from strict_openapi import assert_strict_openapi_response
from user_account_audit_support import (
    MISSING_USER_ID,
    OTHER_SCOPE,
    DisposableMember,
    MintEmailChangeToken,
    UserAccountAuditClient,
    unused_email,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/userAccount/validateEmailChange"


def test_valid_token_changes_email_and_is_single_use(
    user_account_audit_client: UserAccountAuditClient,
    email_change_token: MintEmailChangeToken,
    disposable_member: DisposableMember,
    users_client: UsersClient,
) -> None:
    new_email = unused_email("spec-audit-changed")
    token = email_change_token(disposable_member.user_id, new_email)

    resp = user_account_audit_client.validate_email_change(token=token)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"message": "Email updated successfully"}

    stored = users_client.get_user_email(disposable_member.user_id)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json()["email"] == new_email

    # The 200 records an activity newer than the token's iat, which retires the link.
    replay = user_account_audit_client.validate_email_change(token=token)
    assert replay.status_code == 401, replay.text[:500]
    assert_strict_openapi_response(replay, ROUTE)
    assert "link expired" in replay.json()["error"]["message"]


def test_email_owned_by_another_account_is_bad_request(
    user_account_audit_client: UserAccountAuditClient,
    email_change_token: MintEmailChangeToken,
    disposable_member: DisposableMember,
    users_client: UsersClient,
) -> None:
    # The uniqueness check runs before any lookup of the token's own user, so an
    # id that matches nobody reaches it and no account can be modified. A fresh id:
    # a stale activity row for a fixed one could retire the token first (401).
    token = email_change_token(uuid.uuid4().hex[:24], disposable_member.email)

    resp = user_account_audit_client.validate_email_change(token=token)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"]["message"] == (
        f"Email already in use: {disposable_member.email}"
    )

    stored = users_client.get_user_email(disposable_member.user_id)
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json()["email"] == disposable_member.email


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
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    if message is not None:
        assert resp.json()["error"]["message"] == message
    else:
        assert resp.json()["error"]["message"] in ("Invalid token", "Invalid scope")


def test_token_with_another_scope_is_unauthorized(
    user_account_audit_client: UserAccountAuditClient,
    email_change_token: MintEmailChangeToken,
) -> None:
    token = email_change_token(MISSING_USER_ID, unused_email(), scopes=[OTHER_SCOPE])

    resp = user_account_audit_client.validate_email_change(token=token)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"]["message"] == "Invalid scope"
