"""Strict OpenAPI audit of POST /api/v1/users/:id/resend-invite."""

from __future__ import annotations

import pytest
from helper import mailpit
from helper.clients.org_client import OrgClient
from helper.clients.users_client import UsersClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from users_audit_support import (
    INVALID_BEARER,
    MALFORMED_USER_ID,
    MISSING_USER_ID,
    SeededUser,
    SeedUser,
    request_as,
    unique_email,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/:id/resend-invite"
SENT = {"message": "Invite sent successfully"}


@pytest.fixture
def past_route_gates(smtp_configured: None, org_client: OrgClient) -> None:
    """The controller is reached only with SMTP configured and a non-individual org.

    smtpConfigCheck answers 404 and accountTypeCheck 400 before the controller
    runs. The suite's org is an enterprise one; switching it would break every
    other agent's invite tests, so an individual org is a failure here, not a skip.
    """
    resp = org_client.get_organization()
    assert resp.status_code == 200, resp.text[:300]
    assert resp.json().get("accountType") != "individual", (
        "the shared org is an individual account; accountTypeCheck refuses every call"
    )


@pytest.fixture
def pending_user(
    seed_user: SeedUser, mail_sink: list[str], past_route_gates: None, smtp_relay_reachable: None
) -> SeededUser:
    """A member who never logged in, on a reserved domain so the relay delivers nowhere real."""
    address = unique_email("example.com")
    mail_sink.append(address)
    return seed_user(email=address)


@pytest.mark.parametrize("caller", ["admin", "member"])
def test_an_invite_is_resent_to_a_pending_user(
    users_client: UsersClient, second_user: SecondUser, pending_user: SeededUser, caller: str
) -> None:
    # No admin gate on this route, so a plain member may resend too.
    seen = mailpit.message_ids(pending_user["email"])
    path = f"/{pending_user['_id']}/resend-invite"
    resp = users_client.post(path) if caller == "admin" else request_as(second_user, "POST", path)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == SENT
    mailpit.wait_for_new_message(pending_user["email"], "You are invited to join", seen)


def test_a_body_and_query_are_ignored(users_client: UsersClient, pending_user: SeededUser) -> None:
    with outside_request_contract("the route reads no body or query; this shows both are ignored"):
        resp = users_client.post(
            f"/{pending_user['_id']}/resend-invite",
            params={"email": "spec-audit-elsewhere@example.com"},
            json={"email": "spec-audit-elsewhere@example.com"},
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert mailpit.message_ids("spec-audit-elsewhere@example.com") == set()


@pytest.mark.parametrize("target", ["unknown", "deleted"])
def test_an_unknown_or_deleted_user_is_unauthorized(
    users_client: UsersClient, seed_user: SeedUser, past_route_gates: None, target: str
) -> None:
    # The controller throws UnauthorizedError, not NotFoundError, for a user it cannot find.
    user_id = MISSING_USER_ID
    if target == "deleted":
        user_id = seed_user()["_id"]
        assert users_client.delete_user(user_id).status_code == 200
    resp = users_client.post(f"/{user_id}/resend-invite")
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "Error getting the user" in resp.text, resp.text[:500]


def test_a_user_who_already_logged_in_is_a_bad_request(
    users_client: UsersClient, second_user: SecondUser, past_route_gates: None
) -> None:
    resp = users_client.post(f"/{second_user.user_id}/resend-invite")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "User has already accepted the invite" in resp.text, resp.text[:500]


def test_a_malformed_id_is_a_validation_error(users_client: UsersClient) -> None:
    # Id validation runs before smtpConfigCheck, so this holds whatever the SMTP state.
    resp = users_client.post(f"/{MALFORMED_USER_ID}/resend-invite")
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("headers", "message"),
    [({}, "No token provided"), (INVALID_BEARER, "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    users_client: UsersClient, headers: dict[str, str], message: str
) -> None:
    resp = users_client.post(f"/{MISSING_USER_ID}/resend-invite", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
