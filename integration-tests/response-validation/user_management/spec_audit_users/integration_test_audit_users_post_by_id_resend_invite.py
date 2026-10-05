"""Strict OpenAPI audit of POST /api/v1/users/:id/resend-invite."""

from __future__ import annotations

import uuid

import pytest
from users_audit_support import (
    MALFORMED_USER_ID,
    MISSING_USER_ID,
    SeedUser,
    request_as,
)
from helper.clients.org_client import OrgClient
from helper.clients.users_client import UsersClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/:id/resend-invite"


@pytest.fixture
def past_route_gates(smtp_configured: None, org_client: OrgClient) -> None:
    """The controller is reached only with SMTP configured and a non-individual org.

    smtpConfigCheck answers 404 and accountTypeCheck 400 before the controller
    runs, so the controller's own statuses cannot be pinned without both.
    """
    resp = org_client.get_organization()
    if resp.status_code == 200 and resp.json().get("accountType") == "individual":
        pytest.skip("accountTypeCheck rejects every call for an individual account")


def test_resend_invite_without_token_is_unauthorized(
    users_client: UsersClient,
) -> None:
    resp = users_client.post(f"/{MISSING_USER_ID}/resend-invite", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_resend_invite_malformed_id_fails_validation(
    users_client: UsersClient,
) -> None:
    # Id validation runs before smtpConfigCheck, so this holds whatever the SMTP state.
    resp = users_client.post(f"/{MALFORMED_USER_ID}/resend-invite")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_resend_invite_unknown_user_is_unauthorized(
    users_client: UsersClient, past_route_gates: None
) -> None:
    # The controller throws UnauthorizedError, not NotFoundError, for an unknown id.
    resp = users_client.post(f"/{MISSING_USER_ID}/resend-invite")
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_resend_invite_to_logged_in_user_is_rejected(
    users_client: UsersClient, second_user: SecondUser, past_route_gates: None
) -> None:
    resp = users_client.post(f"/{second_user.user_id}/resend-invite")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_resends_invite_to_pending_user(
    second_user: SecondUser,
    seed_user: SeedUser,
    past_route_gates: None,
    smtp_relay_reachable: None,
) -> None:
    # Reserved domain: a real relay accepts the submission without delivering mail.
    pending = seed_user(email=f"spec-audit-{uuid.uuid4().hex[:10]}@example.com")

    # No admin gate on this route, so a plain member is the stricter caller.
    resp = request_as(second_user, "POST", f"/{pending['_id']}/resend-invite")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"message": "Invite sent successfully"}
