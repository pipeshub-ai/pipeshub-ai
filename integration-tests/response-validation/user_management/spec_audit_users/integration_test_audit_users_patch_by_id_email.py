"""Strict OpenAPI audit of PATCH /api/v1/users/{id}/email."""

from __future__ import annotations

from typing import Any

import pytest
from helper import mailpit
from helper.clients.users_client import UsersClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from users_audit_support import (
    INVALID_BEARER,
    MALFORMED_USER_ID,
    MISSING_USER_ID,
    SeedUser,
    mail_ids_to,
    request_as,
    unique_email,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/:id/email"


def _path(user_id: str) -> str:
    return f"/{user_id}/email"


def test_the_owner_requests_a_change_and_a_link_is_mailed(
    second_user: SecondUser, users_client: UsersClient, mail_sink: list[str]
) -> None:
    new_address = unique_email("example.com")
    mail_sink.extend([new_address, second_user.email])
    seen = mail_ids_to([new_address])

    resp = request_as(second_user, "PATCH", _path(second_user.user_id), json={"email": new_address})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"email": second_user.email, "emailChangeMailStatus": "sent"}

    mailpit.wait_for_new_message(new_address, "Verify your email", seen)
    stored = users_client.get(f"/{second_user.user_id}")
    assert stored.json()["email"] == second_user.email, "the address must not change before the link is opened"


@pytest.mark.parametrize("variant", ["same", "upper-case"])
def test_the_stored_address_is_not_a_change(
    users_client: UsersClient, seed_user: SeedUser, variant: str
) -> None:
    user = seed_user()
    sent = user["email"] if variant == "same" else user["email"].upper()
    resp = users_client.patch(_path(user["_id"]), json={"email": sent})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"email": user["email"], "emailChangeMailStatus": "notNeeded"}


def test_surrounding_whitespace_is_trimmed_before_validation(
    users_client: UsersClient, seed_user: SeedUser
) -> None:
    user = seed_user()
    with outside_request_contract("a padded address, which the global trimming middleware repairs"):
        resp = users_client.patch(_path(user["_id"]), json={"email": f"  {user['email']} "})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["emailChangeMailStatus"] == "notNeeded", resp.json()


def test_an_admin_cannot_change_someone_elses_address(
    users_client: UsersClient, seed_user: SeedUser
) -> None:
    user = seed_user()
    resp = users_client.patch(_path(user["_id"]), json={"email": unique_email("example.com")})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "Only the account owner" in resp.text, resp.text[:500]


def test_an_address_held_by_another_user_is_a_bad_request(
    second_user: SecondUser, seed_user: SeedUser
) -> None:
    taken = seed_user()["email"]
    resp = request_as(second_user, "PATCH", _path(second_user.user_id), json={"email": taken})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "Email already exists for another user" in resp.text, resp.text[:500]


def test_other_body_fields_are_ignored(users_client: UsersClient, seed_user: SeedUser) -> None:
    user = seed_user()
    with outside_request_contract("undocumented body fields, to show they are not applied"):
        resp = users_client.patch(
            _path(user["_id"]), json={"email": user["email"], "fullName": "Hijacked"}
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    stored = users_client.get(f"/{user['_id']}")
    assert stored.json()["fullName"] == user["fullName"], stored.json()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": ""},
        {"email": "not-a-valid-email"},
        {"email": "spec@audit"},
        {"email": 42},
        {"email": None},
        {"newEmail": "spec-audit@example.com"},
    ],
    ids=["missing", "empty", "no-at", "no-tld", "number", "null", "other-name-field"],
)
def test_an_invalid_body_is_a_validation_error(
    users_client: UsersClient, body: dict[str, Any]
) -> None:
    resp = users_client.patch(_path(MISSING_USER_ID), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_malformed_id_is_a_validation_error(users_client: UsersClient) -> None:
    resp = users_client.patch(_path(MALFORMED_USER_ID), json={"email": "spec-audit@example.com"})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("target", ["admin", "unknown"])
def test_a_member_changing_someone_else_is_a_bad_request(
    second_user: SecondUser, pipeshub_client: PipeshubClient, target: str
) -> None:
    user_id = pipeshub_client.acting_user_id if target == "admin" else MISSING_USER_ID
    resp = request_as(second_user, "PATCH", _path(user_id), json={"email": "spec-audit@example.com"})
    assert resp.status_code == 400, resp.text[:500]
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
    resp = users_client.patch(_path(user_id), json={"email": "spec-audit@example.com"})
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
        _path(MISSING_USER_ID), auth=False, headers=headers, json={"email": "spec-audit@example.com"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
