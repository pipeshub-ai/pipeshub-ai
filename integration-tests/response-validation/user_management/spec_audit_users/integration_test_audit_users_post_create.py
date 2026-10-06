"""Strict OpenAPI audit of POST /api/v1/users."""

from __future__ import annotations

from typing import Any, Callable, Iterator

import pytest
import requests
from helper.clients.users_client import UsersClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)
from users_audit_support import (
    DEMO_EMAIL_DOMAIN,
    INVALID_BEARER,
    STRONG_PASSWORD,
    SeedUser,
    delete_credentials,
    read_credentials,
    request_as,
    unique_email,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users"

Create = Callable[[dict[str, Any]], requests.Response]


@pytest.fixture
def create(users_client: UsersClient) -> Iterator[Create]:
    """POST /users with exactly this body; whatever it creates is deleted afterwards."""
    created: list[dict[str, Any]] = []

    def _create(body: dict[str, Any]) -> requests.Response:
        resp = users_client.post("/", json=body)
        if resp.status_code == 201:
            created.append(resp.json())
        return resp

    try:
        yield _create
    finally:
        failures = []
        for user in created:
            user_id = str(user["_id"])
            delete_credentials(user_id)
            # An admin cannot be deleted until demoted.
            if user.get("role") == "admin":
                users_client.update_user(user_id, role="member")
            resp = users_client.delete_user(user_id)
            if resp.status_code >= 400 and resp.status_code != 404:
                failures.append(f"{user_id}: {resp.status_code} {resp.text[:200]}")
        assert not failures, failures


def test_create_with_only_the_required_fields(create: Create) -> None:
    email = unique_email()
    resp = create({"fullName": "Spec Audit Minimal", "email": email})
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    user = resp.json()
    assert (user["email"], user["role"], user["hasLoggedIn"]) == (email, "member", False), user
    assert read_credentials(user["_id"]) == []


def test_create_with_every_optional_field(create: Create) -> None:
    resp = create(
        {
            "fullName": "Spec Audit Full",
            "email": unique_email(),
            "role": "admin",
            "mobile": "+15551234567",
            "designation": "Auditor",
        }
    )
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    user = resp.json()
    assert (user["role"], user["mobile"], user["designation"]) == ("admin", "+15551234567", "Auditor")


def test_an_empty_mobile_is_accepted(create: Create) -> None:
    resp = create({"fullName": "Spec Audit Mobile", "email": unique_email(), "mobile": ""})
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_demo_persona_gets_a_starting_password(create: Create) -> None:
    resp = create(
        {
            "fullName": "Spec Audit Demo",
            "email": unique_email(DEMO_EMAIL_DOMAIN.upper()),
            "password": STRONG_PASSWORD,
        }
    )
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "password" not in resp.json()
    (credential,) = read_credentials(resp.json()["_id"])
    assert credential["hashedPassword"] != STRONG_PASSWORD


def test_an_unknown_body_field_is_dropped(create: Create) -> None:
    with outside_request_contract("an undocumented body field, to show it is dropped"):
        resp = create(
            {"fullName": "Spec Audit Extra", "email": unique_email(), "isDisabled": True, "extra": 1}
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 201, resp.text[:500]
    assert "extra" not in resp.json()
    assert resp.json()["isDisabled"] is False, resp.json()


@pytest.mark.parametrize(
    "body",
    [
        {"email": "x@test-pipeshub.com"},
        {"fullName": "", "email": "x@test-pipeshub.com"},
        {"fullName": "No Email"},
        {"fullName": "Bad Email", "email": "not-an-email"},
        {"fullName": "Bad Email", "email": "a@b"},
        {"fullName": "Bad Role", "email": "x@test-pipeshub.com", "role": "owner"},
        {"fullName": "Bad Mobile", "email": "x@test-pipeshub.com", "mobile": "12345"},
        {"fullName": "Bad Password", "email": f"x@{DEMO_EMAIL_DOMAIN}", "password": 12345678},
    ],
    ids=[
        "missing-fullName",
        "empty-fullName",
        "missing-email",
        "email-no-at",
        "email-no-tld",
        "unknown-role",
        "short-mobile",
        "password-not-a-string",
    ],
)
def test_an_invalid_body_is_a_validation_error(create: Create, body: dict[str, Any]) -> None:
    resp = create(body)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("email_domain", "password", "message"),
    [
        ("test-pipeshub.com", STRONG_PASSWORD, "can only be set for demo accounts"),
        (DEMO_EMAIL_DOMAIN, "weakweak", "Password must be at least 8 characters"),
        (DEMO_EMAIL_DOMAIN, "Sh0rt#", "Password must be at least 8 characters"),
        (DEMO_EMAIL_DOMAIN, "Aa1#" + "x" * 69, "Password must be at least 8 characters"),
        (DEMO_EMAIL_DOMAIN, "Spec Audit 1", "Password must be at least 8 characters"),
    ],
    ids=["not-a-demo-address", "no-upper-digit-special", "too-short", "over-72-bytes", "space-not-special"],
)
def test_a_refused_starting_password_is_a_bad_request(
    create: Create, email_domain: str, password: str, message: str
) -> None:
    resp = create(
        {"fullName": "Spec Audit Password", "email": unique_email(email_domain), "password": password}
    )
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert message in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_a_taken_email_is_a_bad_request(create: Create, seed_user: SeedUser) -> None:
    taken = seed_user()["email"]
    resp = create({"fullName": "Spec Audit Duplicate", "email": taken})
    assert resp.status_code == 400, resp.text[:500]
    assert "A user with this email already exists" in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "/", json={"fullName": "Nope", "email": unique_email()})
    assert resp.status_code == 403, resp.text[:500]
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
        "/", auth=False, headers=headers, json={"fullName": "Nope", "email": unique_email()}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
