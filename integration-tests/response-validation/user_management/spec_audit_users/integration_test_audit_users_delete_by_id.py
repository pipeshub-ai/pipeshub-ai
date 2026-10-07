"""Strict OpenAPI audit of DELETE /api/v1/users/:id."""

from __future__ import annotations

import pytest
from helper.clients.users_client import UsersClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from users_audit_support import (
    INVALID_BEARER,
    MALFORMED_USER_ID,
    MISSING_USER_ID,
    SeedUser,
    request_as,
    users_with_emails,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/:id"


def test_admin_soft_deletes_a_member(users_client: UsersClient, seed_user: SeedUser) -> None:
    user = seed_user()
    resp = users_client.delete(f"/{user['_id']}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": "User deleted successfully"}
    (stored,) = users_with_emails([user["email"]])
    assert (stored["isDeleted"], stored["hasLoggedIn"]) == (True, False), stored


def test_deleting_again_is_not_found(users_client: UsersClient, seed_user: SeedUser) -> None:
    user = seed_user()
    first = users_client.delete(f"/{user['_id']}")
    assert first.status_code == 200, first.text[:500]
    resp = users_client.delete(f"/{user['_id']}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "User not found" in resp.text, resp.text[:500]


def test_a_body_and_query_are_ignored(users_client: UsersClient, seed_user: SeedUser) -> None:
    user = seed_user()
    with outside_request_contract("the route reads no body or query; this shows both are ignored"):
        resp = users_client.delete(
            f"/{user['_id']}", params={"hard": "true"}, json={"reason": "spec audit"}
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    (stored,) = users_with_emails([user["email"]])
    assert stored["isDeleted"] is True, stored


def test_an_admin_cannot_be_deleted(users_client: UsersClient, pipeshub_client: PipeshubClient) -> None:
    # Only role 'member' is deletable; the suite's own admin is the safe admin target.
    resp = users_client.delete(f"/{pipeshub_client.acting_user_id}")
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "demote the user from admin first" in resp.text, resp.text[:500]


@pytest.mark.parametrize("target", ["member", "unknown"])
def test_a_member_cannot_delete_anyone(
    second_user: SecondUser, seed_user: SeedUser, target: str
) -> None:
    # The admin check runs before the lookup, so an unknown id is refused the same way.
    user_id = seed_user()["_id"] if target == "member" else MISSING_USER_ID
    resp = request_as(second_user, "DELETE", f"/{user_id}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "You need admin access" in resp.text, resp.text[:500]


def test_an_unknown_user_is_not_found(users_client: UsersClient) -> None:
    resp = users_client.delete(f"/{MISSING_USER_ID}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "User not found" in resp.text, resp.text[:500]


def test_a_malformed_id_is_a_validation_error(users_client: UsersClient) -> None:
    resp = users_client.delete(f"/{MALFORMED_USER_ID}")
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("headers", "message"),
    [({}, "No token provided"), (INVALID_BEARER, "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    users_client: UsersClient, seed_user: SeedUser, headers: dict[str, str], message: str
) -> None:
    user = seed_user()
    resp = users_client.delete(f"/{user['_id']}", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
    (stored,) = users_with_emails([user["email"]])
    assert stored["isDeleted"] is False, stored
