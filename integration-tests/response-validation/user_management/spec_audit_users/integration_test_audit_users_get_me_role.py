"""Strict OpenAPI audit of GET /api/v1/users/me/role."""

from __future__ import annotations

import pytest
from users_audit_support import (
    INVALID_BEARER,
    WRONG_SECRET,
    bearer,
    mint_scoped_token,
    request_as,
)
from helper.clients.users_client import UsersClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser, create_second_user, delete_second_user
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/me/role"


def test_admin_reads_own_role(users_client: UsersClient) -> None:
    resp = users_client.get("/me/role")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"role": "admin"}


def test_member_reads_own_role(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/me/role")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"role": "member"}


def test_query_parameters_are_ignored(users_client: UsersClient) -> None:
    with outside_request_contract("the route takes no query; this shows extra ones are ignored"):
        resp = users_client.get("/me/role", params={"role": "member"})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {"role": "admin"}


def test_a_deleted_users_token_is_unauthorized(pipeshub_client: PipeshubClient) -> None:
    user = create_second_user(pipeshub_client)
    delete_second_user(pipeshub_client, user, strict=True)
    resp = request_as(user, "GET", "/me/role")
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def _forged_session_headers(client: PipeshubClient) -> dict[str, str]:
    # Carries every claim a session token has, so only the signature can refuse it.
    token = mint_scoped_token(
        WRONG_SECRET,
        None,
        userId=client.acting_user_id,
        orgId=client.org_id,
        role="admin",
    )
    return bearer(token)


@pytest.mark.parametrize(
    ("credentials", "message"),
    [
        pytest.param("none", "No token provided", id="no-token"),
        pytest.param("garbage", "Invalid token", id="not-a-jwt"),
        pytest.param("forged", "Invalid token", id="forged-signature"),
    ],
)
def test_rejected_credentials_are_unauthorized(
    users_client: UsersClient,
    pipeshub_client: PipeshubClient,
    credentials: str,
    message: str,
) -> None:
    headers: dict[str, str] = {}
    if credentials == "garbage":
        headers = INVALID_BEARER
    elif credentials == "forged":
        headers = _forged_session_headers(pipeshub_client)

    resp = users_client.get("/me/role", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
