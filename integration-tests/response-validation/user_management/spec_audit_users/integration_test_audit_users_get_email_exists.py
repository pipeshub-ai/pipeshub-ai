"""Strict OpenAPI audit of GET /api/v1/users/email/exists."""

from __future__ import annotations

import uuid

import pytest
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from users_audit_support import (
    OTHER_SCOPE,
    USER_LOOKUP_SCOPE,
    WRONG_SECRET,
    MintScopedToken,
    SeedUser,
    mint_scoped_token,
    request_with_token,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/email/exists"
PATH = "/email/exists"


def test_email_exists_returns_the_matching_user_documents(
    pipeshub_client: PipeshubClient,
    scoped_token: MintScopedToken,
    seed_user: SeedUser,
) -> None:
    user = seed_user()

    # The controller reads the address from the JSON body of the GET, not the query.
    resp = request_with_token(
        pipeshub_client,
        scoped_token(USER_LOOKUP_SCOPE),
        "GET",
        PATH,
        json={"email": user["email"]},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert isinstance(body, list), body
    assert [doc["_id"] for doc in body] == [user["_id"]], body
    assert body[0]["email"] == user["email"], body


def test_email_exists_returns_an_empty_array_for_an_unknown_address(
    pipeshub_client: PipeshubClient, scoped_token: MintScopedToken
) -> None:
    unknown = f"spec-audit-absent-{uuid.uuid4().hex[:10]}@test-pipeshub.com"
    resp = request_with_token(
        pipeshub_client,
        scoped_token(USER_LOOKUP_SCOPE),
        "GET",
        PATH,
        json={"email": unknown},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == []


def test_email_exists_without_a_body_is_a_validation_error(
    pipeshub_client: PipeshubClient, scoped_token: MintScopedToken
) -> None:
    # An email in the query string is ignored: only the body is validated.
    resp = request_with_token(
        pipeshub_client,
        scoped_token(USER_LOOKUP_SCOPE),
        "GET",
        PATH,
        params={"email": "someone@test-pipeshub.com"},
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_email_exists_ignores_an_unknown_body_field(
    pipeshub_client: PipeshubClient,
    scoped_token: MintScopedToken,
    seed_user: SeedUser,
) -> None:
    user = seed_user()
    with outside_request_contract("an undocumented body field, to show it is ignored"):
        resp = request_with_token(
            pipeshub_client,
            scoped_token(USER_LOOKUP_SCOPE),
            "GET",
            PATH,
            json={"email": user["email"], "orgId": "someone-else"},
        )
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert [doc["_id"] for doc in resp.json()] == [user["_id"]]


@pytest.mark.parametrize(
    "email",
    ["not-an-email", "a@b", "", "a..b@test-pipeshub.com", 42],
    ids=["no-at", "no-tld", "empty", "double-dot", "number"],
)
def test_email_exists_with_an_invalid_address_is_a_validation_error(
    pipeshub_client: PipeshubClient, scoped_token: MintScopedToken, email: object
) -> None:
    resp = request_with_token(
        pipeshub_client, scoped_token(USER_LOOKUP_SCOPE), "GET", PATH, json={"email": email}
    )
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("token_kind", ["expired", "wrong-secret"])
def test_email_exists_rejects_an_expired_or_forged_token(
    pipeshub_client: PipeshubClient, scoped_token: MintScopedToken, token_kind: str
) -> None:
    if token_kind == "expired":
        token = scoped_token(USER_LOOKUP_SCOPE, ttl_seconds=-60)
    else:
        token = mint_scoped_token(
            WRONG_SECRET,
            [USER_LOOKUP_SCOPE],
            userId=pipeshub_client.acting_user_id,
            orgId=pipeshub_client.org_id,
        )
    resp = request_with_token(
        pipeshub_client, token, "GET", PATH, json={"email": "someone@test-pipeshub.com"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_email_exists_rejects_a_token_without_the_lookup_scope(
    pipeshub_client: PipeshubClient, scoped_token: MintScopedToken
) -> None:
    resp = request_with_token(
        pipeshub_client,
        scoped_token(OTHER_SCOPE),
        "GET",
        PATH,
        json={"email": "someone@test-pipeshub.com"},
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_email_exists_rejects_a_call_without_a_token(
    pipeshub_client: PipeshubClient,
) -> None:
    resp = pipeshub_client.request(
        "GET", ROUTE, auth=False, json={"email": "someone@test-pipeshub.com"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
