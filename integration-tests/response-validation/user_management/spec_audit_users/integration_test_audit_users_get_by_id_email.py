"""Strict OpenAPI audit of GET /api/v1/users/{id}/email."""

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


ROUTE = "/api/v1/users/:id/email"


def test_admin_reads_a_users_email(users_client: UsersClient, seed_user: SeedUser) -> None:
    user = seed_user()
    resp = users_client.get(f"/{user['_id']}/email")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"email": user["email"]}


def test_query_parameters_are_ignored(users_client: UsersClient, seed_user: SeedUser) -> None:
    user = seed_user()
    with outside_request_contract("the route takes no query; this shows extra ones are ignored"):
        resp = users_client.get(f"/{user['_id']}/email", params={"x": "1"})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]


def test_a_member_is_forbidden(
    second_user: SecondUser, pipeshub_client: PipeshubClient
) -> None:
    resp = request_as(second_user, "GET", f"/{pipeshub_client.acting_user_id}/email")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_malformed_id_is_a_validation_error(users_client: UsersClient) -> None:
    resp = users_client.get(f"/{MALFORMED_USER_ID}/email")
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_an_unknown_user_is_not_found(users_client: UsersClient) -> None:
    resp = users_client.get(f"/{MISSING_USER_ID}/email")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "User not found" in resp.text, resp.text[:500]


@pytest.mark.parametrize(
    ("headers", "message"),
    [({}, "No token provided"), (INVALID_BEARER, "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    users_client: UsersClient, headers: dict[str, str], message: str
) -> None:
    resp = users_client.get(f"/{MISSING_USER_ID}/email", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
