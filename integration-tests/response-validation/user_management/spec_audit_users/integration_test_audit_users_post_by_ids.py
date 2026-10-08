"""Strict OpenAPI audit of POST /api/v1/users/by-ids."""

from __future__ import annotations

from typing import Any

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

ROUTE = "/api/v1/users/by-ids"
PATH = "/by-ids"


def test_admin_reads_users_by_id(users_client: UsersClient, seed_user: SeedUser) -> None:
    first, second = seed_user(), seed_user()
    resp = users_client.post(
        PATH, json={"userIds": [first["_id"], second["_id"], MISSING_USER_ID]}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert sorted(u["_id"] for u in resp.json()) == sorted([first["_id"], second["_id"]])


def test_a_member_may_read_other_users(
    second_user: SecondUser, pipeshub_client: PipeshubClient
) -> None:
    resp = request_as(second_user, "POST", PATH, json={"userIds": [pipeshub_client.acting_user_id]})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    (admin,) = resp.json()
    assert admin["_id"] == pipeshub_client.acting_user_id
    assert admin["email"], admin


def test_only_unknown_ids_give_an_empty_array(users_client: UsersClient) -> None:
    resp = users_client.post(PATH, json={"userIds": [MISSING_USER_ID]})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == []


def test_an_unknown_body_field_is_ignored(
    users_client: UsersClient, pipeshub_client: PipeshubClient
) -> None:
    with outside_request_contract("an undocumented body field, to show it is ignored"):
        resp = users_client.post(
            PATH, json={"userIds": [pipeshub_client.acting_user_id], "fields": ["email"]}
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert len(resp.json()) == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"userIds": []},
        {"userIds": [MALFORMED_USER_ID]},
        {"userIds": [""]},
        {"userIds": MISSING_USER_ID},
        {"userIds": [123]},
        {"ids": [MISSING_USER_ID]},
    ],
    ids=["missing", "empty", "malformed-id", "empty-id", "not-an-array", "number-id", "wrong-name"],
)
def test_an_invalid_body_is_a_validation_error(
    users_client: UsersClient, body: dict[str, Any]
) -> None:
    resp = users_client.post(PATH, json=body)
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
    resp = users_client.post(
        PATH, auth=False, headers=headers, json={"userIds": [MISSING_USER_ID]}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
