"""Strict OpenAPI audit of GET /api/v1/users/email/exists."""

from __future__ import annotations

import uuid

import pytest
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_response
from users_audit_support import (
    OTHER_SCOPE,
    USER_LOOKUP_SCOPE,
    MintScopedToken,
    SeedUser,
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
    assert_strict_openapi_response(resp, ROUTE)
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
    assert_strict_openapi_response(resp, ROUTE)
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
    assert_strict_openapi_response(resp, ROUTE)


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
    assert_strict_openapi_response(resp, ROUTE)


def test_email_exists_rejects_a_call_without_a_token(
    pipeshub_client: PipeshubClient,
) -> None:
    resp = pipeshub_client.request(
        "GET", ROUTE, auth=False, json={"email": "someone@test-pipeshub.com"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
