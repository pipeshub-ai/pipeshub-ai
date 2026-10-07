"""Strict OpenAPI audit of PUT /api/v1/users/:id."""

from __future__ import annotations

from typing import Any

import pytest
from helper import mailpit
from helper.clients.users_client import UsersClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)
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

ROUTE = "/api/v1/users/:id"

ADDRESS = {
    "addressLine1": "1 Spec Audit Road",
    "city": "Pune",
    "state": "Maharashtra",
    "postCode": "411001",
    "country": "India",
}


def test_admin_updates_every_profile_field(users_client: UsersClient, seed_user: SeedUser) -> None:
    user = seed_user()
    body = {
        "fullName": "Spec Audit Updated",
        "firstName": "Spec",
        "middleName": "Audit",
        "lastName": "Updated",
        "designation": "Auditor",
        "mobile": "+919876543210",
        "address": ADDRESS,
        "dataCollectionConsent": True,
        "hasLoggedIn": False,
        "role": "member",
        "email": user["email"],
    }
    resp = users_client.put(f"/{user['_id']}", json=body)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    got = resp.json()
    stored = {k: v for k, v in body.items() if k not in ("address", "dataCollectionConsent")}
    assert {k: got[k] for k in stored} == stored
    assert "dataCollectionConsent" not in got, "the user record has no such field"
    address = dict(got["address"])
    assert address.pop("_id"), got["address"]
    assert address == ADDRESS
    assert got["meta"] == {"emailChangeMailStatus": "notNeeded"}


def test_the_address_id_is_dropped(users_client: UsersClient, seed_user: SeedUser) -> None:
    # GET returns address._id; sending the address back unchanged must not be refused.
    user = seed_user()
    with outside_request_contract("address._id as GET returns it; the validator drops it"):
        resp = users_client.put(
            f"/{user['_id']}", json={"address": {"_id": MISSING_USER_ID, "city": "Pune"}}
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["address"]["city"] == "Pune"
    assert resp.json()["address"]["_id"] != MISSING_USER_ID, resp.json()["address"]


@pytest.mark.parametrize("country", ["Saint Kitts & Nevis", "Czech Republic (Czechia)"])
def test_a_listed_country_is_stored(users_client: UsersClient, seed_user: SeedUser, country: str) -> None:
    user = seed_user()
    resp = users_client.put(f"/{user['_id']}", json={"address": {"country": country}})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["address"]["country"] == country


@pytest.mark.parametrize("country", ["IN", "Narnia", "india"])
def test_a_country_outside_the_list_is_an_internal_error(
    users_client: UsersClient, seed_user: SeedUser, country: str
) -> None:
    user = seed_user()
    resp = users_client.put(f"/{user['_id']}", json={"address": {"country": country}})
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
    assert "address" not in users_client.get(f"/{user['_id']}").json()


@pytest.mark.parametrize("value", ["", " \t "], ids=["empty", "only-whitespace"])
def test_an_empty_full_name_is_stored(
    users_client: UsersClient, seed_user: SeedUser, value: str
) -> None:
    user = seed_user()
    resp = users_client.put(f"/{user['_id']}", json={"fullName": value})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["fullName"] == ""


def test_an_empty_body_is_a_bad_request(users_client: UsersClient, seed_user: SeedUser) -> None:
    user = seed_user()
    resp = users_client.put(f"/{user['_id']}", json={})
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert "No valid fields provided for update" in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        {"_id": MISSING_USER_ID},
        {"orgId": MISSING_USER_ID},
        {"slug": "user-1"},
        {"__v": 1},
        {"isDeleted": True},
        {"designation": "x", "unknown": 1},
        {"email": "not-an-email"},
        {"email": "spec@audit"},
        {"mobile": "12"},
        {"mobile": "+1 555 123 4567"},
        {"role": "owner"},
        {"hasLoggedIn": "yes"},
        {"dataCollectionConsent": 1},
        {"fullName": 5},
        {"middleName": None},
        {"address": "Pune"},
        {"address": {"city": 5}},
    ],
    ids=[
        "restricted-_id",
        "restricted-orgId",
        "restricted-slug",
        "restricted-__v",
        "unknown-isDeleted",
        "unknown-field",
        "email-no-at",
        "email-no-tld",
        "mobile-short",
        "mobile-spaces",
        "role-unknown",
        "hasLoggedIn-string",
        "consent-number",
        "fullName-number",
        "middleName-null",
        "address-string",
        "address-city-number",
    ],
)
def test_an_invalid_body_is_a_validation_error(
    users_client: UsersClient, body: dict[str, Any]
) -> None:
    # Restricted fields never reach the handler's own check: the strict schema refuses them first.
    resp = users_client.put(f"/{MISSING_USER_ID}", json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_member_cannot_set_a_role_even_unchanged(second_user: SecondUser) -> None:
    resp = request_as(second_user, "PUT", f"/{second_user.user_id}", json={"role": "member"})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "Only admins can change user roles" in resp.text, resp.text[:500]


def test_a_member_updates_their_own_profile(second_user: SecondUser) -> None:
    resp = request_as(
        second_user, "PUT", f"/{second_user.user_id}", json={"designation": "Spec Audit Self"}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["designation"] == "Spec Audit Self"


def test_the_owner_changing_the_address_gets_a_link(
    second_user: SecondUser, mail_sink: list[str]
) -> None:
    new_address = unique_email("example.com")
    mail_sink.extend([new_address, second_user.email])
    seen = mail_ids_to([new_address])
    resp = request_as(second_user, "PUT", f"/{second_user.user_id}", json={"email": new_address})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["email"], body["meta"]) == (second_user.email, {"emailChangeMailStatus": "sent"}), body
    mailpit.wait_for_new_message(new_address, "Verify your email", seen)


def test_an_admin_cannot_change_someone_elses_address(
    users_client: UsersClient, seed_user: SeedUser
) -> None:
    user = seed_user()
    resp = users_client.put(f"/{user['_id']}", json={"email": unique_email("example.com")})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "Only the account owner" in resp.text, resp.text[:500]


def test_an_address_held_by_another_user_is_a_bad_request(
    second_user: SecondUser, seed_user: SeedUser
) -> None:
    taken = seed_user()["email"]
    resp = request_as(second_user, "PUT", f"/{second_user.user_id}", json={"email": taken})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "Email already exists for another user" in resp.text, resp.text[:500]


@pytest.mark.parametrize("target", ["admin", "unknown"])
def test_a_member_changing_someone_else_is_a_bad_request(
    second_user: SecondUser, pipeshub_client: PipeshubClient, target: str
) -> None:
    user_id = pipeshub_client.acting_user_id if target == "admin" else MISSING_USER_ID
    resp = request_as(second_user, "PUT", f"/{user_id}", json={"designation": "Nope"})
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
    resp = users_client.put(f"/{user_id}", json={"designation": "Spec Audit"})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "User not found" in resp.text, resp.text[:500]


def test_a_malformed_id_is_a_validation_error(users_client: UsersClient) -> None:
    resp = users_client.put(f"/{MALFORMED_USER_ID}", json={"designation": "Spec Audit"})
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
    resp = users_client.put(
        f"/{MISSING_USER_ID}", auth=False, headers=headers, json={"designation": "Spec Audit"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
