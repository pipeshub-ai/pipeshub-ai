"""Strict OpenAPI audit of GET /api/v1/users/:id/adminCheck."""

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
    request_as,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/:id/adminCheck"
ADMIN = {"message": "User has admin access"}


@pytest.mark.parametrize("target", ["self", "member", "unknown"])
def test_an_admin_gets_200_whatever_the_id(
    users_client: UsersClient,
    pipeshub_client: PipeshubClient,
    second_user: SecondUser,
    target: str,
) -> None:
    # The id is only format-checked: the answer is about the caller, not the user it names.
    user_id = {
        "self": pipeshub_client.acting_user_id,
        "member": second_user.user_id,
        "unknown": MISSING_USER_ID,
    }[target]
    resp = users_client.get(f"/{user_id}/adminCheck")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == ADMIN


@pytest.mark.parametrize("target", ["self", "admin"])
def test_a_member_is_forbidden_whatever_the_id(
    second_user: SecondUser, pipeshub_client: PipeshubClient, target: str
) -> None:
    user_id = second_user.user_id if target == "self" else pipeshub_client.acting_user_id
    resp = request_as(second_user, "GET", f"/{user_id}/adminCheck")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "You need admin access" in resp.text, resp.text[:500]


def test_a_query_is_ignored(users_client: UsersClient) -> None:
    with outside_request_contract("the route takes no query; this shows extra ones are ignored"):
        resp = users_client.get(f"/{MISSING_USER_ID}/adminCheck", params={"orgId": MISSING_USER_ID})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]


def test_a_malformed_id_is_a_validation_error(users_client: UsersClient) -> None:
    resp = users_client.get(f"/{MALFORMED_USER_ID}/adminCheck")
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
    resp = users_client.get(f"/{MISSING_USER_ID}/adminCheck", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
