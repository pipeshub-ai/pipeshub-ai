"""Strict OpenAPI audit of GET /api/v1/users/:id."""

from __future__ import annotations

import pytest
from helper.clients.users_client import UsersClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response
from users_audit_support import (
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
    assert_strict_openapi_response(resp, ROUTE)


def test_get_user_by_id_is_readable_by_non_admin_member(
    second_user: SecondUser, pipeshub_client: PipeshubClient
) -> None:
    # No admin gate on this route: a member may read another user of the org.
    admin_id = pipeshub_client.acting_user_id

    resp = request_as(second_user, "GET", f"/{admin_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["_id"] == admin_id
    assert_strict_openapi_response(resp, ROUTE)


def test_get_user_by_id_without_token_is_unauthorized(
    users_client: UsersClient,
) -> None:
    resp = users_client.get(f"/{MISSING_USER_ID}", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("user_id", "expected_status"),
    [
        pytest.param(MALFORMED_USER_ID, 400, id="malformed-id"),
        pytest.param(MISSING_USER_ID, 404, id="missing-id"),
    ],
)
def test_get_user_by_id_rejects_unusable_id(
    users_client: UsersClient, user_id: str, expected_status: int
) -> None:
    resp = users_client.get_user(user_id)

    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
