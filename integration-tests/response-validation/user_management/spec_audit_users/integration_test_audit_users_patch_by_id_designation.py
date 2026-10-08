"""Strict OpenAPI audit of PATCH /api/v1/users/{id}/designation."""

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

ROUTE = "/api/v1/users/:id/designation"
FIELD = "designation"


def _path(user_id: str) -> str:
    return f"/{user_id}/designation"


def test_admin_sets_a_users_designation(users_client: UsersClient, seed_user: SeedUser) -> None:
    user = seed_user()
    resp = users_client.patch(_path(user["_id"]), json={FIELD: "Spec Audit Engineer"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["_id"], body[FIELD]) == (user["_id"], "Spec Audit Engineer"), body
    stored = users_client.get(f"/{user['_id']}")
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json()[FIELD] == "Spec Audit Engineer", stored.json()


def test_surrounding_whitespace_is_trimmed(users_client: UsersClient, seed_user: SeedUser) -> None:
    user = seed_user()
    resp = users_client.patch(_path(user["_id"]), json={FIELD: "  Spec Audit Padded \t"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()[FIELD] == "Spec Audit Padded", resp.json()


def test_html_in_the_value_is_refused_before_validation(
    users_client: UsersClient, seed_user: SeedUser
) -> None:
    user = seed_user()
    resp = users_client.patch(_path(user["_id"]), json={FIELD: "<b>Spec Audit</b>"})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert "HTML tags" in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    stored = users_client.get(f"/{user['_id']}")
    assert FIELD not in stored.json(), stored.json()


def test_a_member_sets_their_own_designation(second_user: SecondUser) -> None:
    resp = request_as(
        second_user, "PATCH", _path(second_user.user_id), json={FIELD: "Spec Audit Self"}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()[FIELD] == "Spec Audit Self", resp.json()


def test_other_body_fields_are_ignored(users_client: UsersClient, seed_user: SeedUser) -> None:
    user = seed_user()
    with outside_request_contract("undocumented body fields, to show they are not applied"):
        resp = users_client.patch(
            _path(user["_id"]),
            json={FIELD: "Spec Audit Extra", "fullName": "Hijacked", "role": "admin"},
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert (body[FIELD], body["fullName"], body["role"]) == (
        "Spec Audit Extra",
        user["fullName"],
        "member",
    ), body


def test_query_parameters_are_ignored(users_client: UsersClient, seed_user: SeedUser) -> None:
    user = seed_user()
    with outside_request_contract("the route takes no query; this shows extra ones are ignored"):
        resp = users_client.patch(
            _path(user["_id"]), params={FIELD: "From Query"}, json={FIELD: "From Body"}
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()[FIELD] == "From Body", resp.json()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {FIELD: ""},
        {FIELD: " \t "},
        {FIELD: 42},
        {FIELD: None},
        {FIELD: ["x"]},
        {"title": "Wrong Field"},
    ],
    ids=["missing", "empty", "only-whitespace", "number", "null", "array", "other-name-field"],
)
def test_an_invalid_body_is_a_validation_error(
    users_client: UsersClient, body: dict[str, Any]
) -> None:
    resp = users_client.patch(_path(MISSING_USER_ID), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_malformed_id_is_a_validation_error(users_client: UsersClient) -> None:
    resp = users_client.patch(_path(MALFORMED_USER_ID), json={FIELD: "Spec Audit"})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("target", ["admin", "unknown"])
def test_a_member_changing_someone_else_is_a_bad_request(
    second_user: SecondUser, pipeshub_client: PipeshubClient, target: str
) -> None:
    # The admin-or-self check runs before the lookup, so an unknown id is refused the same way.
    user_id = pipeshub_client.acting_user_id if target == "admin" else MISSING_USER_ID
    resp = request_as(second_user, "PATCH", _path(user_id), json={FIELD: "Spec Audit Nope"})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert "dont have admin access" in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("target", ["unknown", "deleted"])
def test_an_unknown_or_deleted_user_is_not_found(
    users_client: UsersClient, seed_user: SeedUser, target: str
) -> None:
    user_id = MISSING_USER_ID
    if target == "deleted":
        user_id = seed_user()["_id"]
        deleted = users_client.delete_user(user_id)
        assert deleted.status_code == 200, deleted.text[:500]
    resp = users_client.patch(_path(user_id), json={FIELD: "Spec Audit"})
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
    resp = users_client.patch(
        _path(MISSING_USER_ID), auth=False, headers=headers, json={FIELD: "Spec Audit"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
