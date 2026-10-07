"""Strict OpenAPI audit of GET /api/v1/users/:id."""

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
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/:id"


def test_get_user_by_id_returns_user_document(
    users_client: UsersClient, seed_user: SeedUser, pipeshub_client: PipeshubClient
) -> None:
    seeded = seed_user()

    resp = users_client.get_user(seeded["_id"])

    assert resp.status_code == 200, resp.text[:500]
    user = resp.json()
    assert user["_id"] == seeded["_id"]
    assert user["orgId"] == pipeshub_client.org_id
    assert user["isDeleted"] is False
    # HIDE_EMAIL=true drops the field from the document.
    assert user.get("email", seeded["email"]) == seeded["email"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_an_empty_full_name_is_returned_as_stored(
    users_client: UsersClient, seed_user: SeedUser
) -> None:
    # PUT /users/:id stores an empty fullName, so readers get one back.
    seeded = seed_user()
    cleared = users_client.put(f"/{seeded['_id']}", json={"fullName": ""})
    assert cleared.status_code == 200, cleared.text[:500]

    resp = users_client.get_user(seeded["_id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["fullName"] == "", resp.json()


def test_get_user_by_id_is_readable_by_non_admin_member(
    second_user: SecondUser, pipeshub_client: PipeshubClient
) -> None:
    # No admin gate on this route: a member may read another user of the org.
    admin_id = pipeshub_client.acting_user_id

    resp = request_as(second_user, "GET", f"/{admin_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["_id"] == admin_id
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_query_is_ignored(users_client: UsersClient, seed_user: SeedUser) -> None:
    seeded = seed_user()
    with outside_request_contract("the route takes no query; this shows extra ones are ignored"):
        resp = users_client.get(f"/{seeded['_id']}", params={"fields": "email"})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["fullName"] == seeded["fullName"], resp.json()


@pytest.mark.parametrize(
    ("headers", "message"),
    [({}, "No token provided"), (INVALID_BEARER, "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    users_client: UsersClient, headers: dict[str, str], message: str
) -> None:
    resp = users_client.get(f"/{MISSING_USER_ID}", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]


@pytest.mark.parametrize("target", ["unknown", "deleted"])
def test_an_unknown_or_deleted_user_is_not_found(
    users_client: UsersClient, seed_user: SeedUser, target: str
) -> None:
    user_id = MISSING_USER_ID
    if target == "deleted":
        user_id = seed_user()["_id"]
        deleted = users_client.delete_user(user_id)
        assert deleted.status_code == 200, deleted.text[:500]
    resp = users_client.get_user(user_id)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "User not found" in resp.text, resp.text[:500]


def test_a_malformed_id_is_a_validation_error(users_client: UsersClient) -> None:
    resp = users_client.get_user(MALFORMED_USER_ID)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
