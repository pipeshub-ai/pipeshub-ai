"""Strict OpenAPI audit of GET /api/v1/users/fetch/with-groups."""

from __future__ import annotations

import pytest
from helper.clients.users_client import UsersClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from users_audit_support import INVALID_BEARER, SeedUser, request_as

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/fetch/with-groups"
PATH = "/fetch/with-groups"


def test_admin_lists_every_user_with_their_groups(
    users_client: UsersClient, seed_user: SeedUser
) -> None:
    user = seed_user()
    resp = users_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert all({"_id", "orgId", "groups"} <= set(item) for item in body), body[:3]
    (seeded,) = [item for item in body if item["_id"] == user["_id"]]
    assert seeded["fullName"] == user["fullName"], seeded
    assert seeded["hasLoggedIn"] is False, seeded
    assert seeded["groups"] == [{"name": "everyone", "type": "everyone"}], seeded


def test_a_member_may_list_them_too(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert any(item["_id"] == second_user.user_id for item in resp.json())


def test_query_parameters_are_ignored(users_client: UsersClient) -> None:
    with outside_request_contract("the route takes no query; this shows extra ones are ignored"):
        resp = users_client.get(PATH, params={"page": "2", "limit": "1"})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert len(resp.json()) > 1


@pytest.mark.parametrize(
    ("headers", "message"),
    [({}, "No token provided"), (INVALID_BEARER, "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    users_client: UsersClient, headers: dict[str, str], message: str
) -> None:
    resp = users_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
