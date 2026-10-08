"""Strict OpenAPI audit of PUT /api/v1/users/{id}/unblock."""

from __future__ import annotations

import pytest
from helper.clients.users_client import UsersClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from users_audit_support import (
    INVALID_BEARER,
    MALFORMED_USER_ID,
    MISSING_USER_ID,
    SeededUser,
    SeedUser,
    read_credentials,
    request_as,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/:id/unblock"


def test_admin_unblocks_a_locked_out_user(
    users_client: UsersClient, blocked_user: SeededUser
) -> None:
    resp = users_client.put(f"/{blocked_user['_id']}/unblock")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": "User unblocked successfully"}
    (credential,) = read_credentials(blocked_user["_id"])
    assert credential["isBlocked"] is False, credential
    assert credential["wrongCredentialCount"] == 0, credential

    again = users_client.put(f"/{blocked_user['_id']}/unblock")
    assert again.status_code == 400, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)
    assert "User not found or not blocked" in again.text, again.text[:500]


def test_a_body_is_ignored(users_client: UsersClient, blocked_user: SeededUser) -> None:
    with outside_request_contract("the route reads no body; this shows one is ignored"):
        resp = users_client.put(f"/{blocked_user['_id']}/unblock", json={"isBlocked": True})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]


@pytest.mark.parametrize("target", ["never-blocked", "unknown"])
def test_a_user_who_is_not_blocked_is_a_bad_request(
    users_client: UsersClient, seed_user: SeedUser, target: str
) -> None:
    user_id = seed_user()["_id"] if target == "never-blocked" else MISSING_USER_ID
    resp = users_client.put(f"/{user_id}/unblock")
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_malformed_id_is_a_validation_error(users_client: UsersClient) -> None:
    resp = users_client.put(f"/{MALFORMED_USER_ID}/unblock")
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_member_is_forbidden(second_user: SecondUser, blocked_user: SeededUser) -> None:
    resp = request_as(second_user, "PUT", f"/{blocked_user['_id']}/unblock")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    (credential,) = read_credentials(blocked_user["_id"])
    assert credential["isBlocked"] is True, credential


@pytest.mark.parametrize(
    ("headers", "message"),
    [({}, "No token provided"), (INVALID_BEARER, "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    users_client: UsersClient, headers: dict[str, str], message: str
) -> None:
    resp = users_client.put(f"/{MISSING_USER_ID}/unblock", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
