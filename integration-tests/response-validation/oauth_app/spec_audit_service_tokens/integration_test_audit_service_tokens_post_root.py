"""Strict OpenAPI audit of POST /api/v1/service-tokens.

authenticate -> rate limiter -> userAdminCheck -> requireScopes(user:invite)
-> zod createServiceTokenSchema (trims the name, strips unknown fields) -> createToken.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from helper.second_user import SecondUser
from service_tokens_audit_support import (
    DEFAULT_EXPIRY_DAYS,
    DEFAULT_TOKEN_SCOPES,
    DENIED_TOKEN_SCOPE,
    MAX_ACTIVE_TOKENS,
    MAX_EXPIRY_DAYS,
    MISSING_SERVICE_ACCOUNT_ID,
    UNKNOWN_TOKEN_SCOPE,
    SeedServiceAccount,
    SeedServiceToken,
    ServiceTokensClient,
    create_token_body,
    request_as,
    request_with_token,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/service-tokens"
SECRET_PREFIX = "phsvc_"


def _parse(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def _lifetime_days(token: dict[str, Any]) -> float:
    return (_parse(token["expiresAt"]) - _parse(token["createdAt"])).total_seconds() / 86400


def test_create_returns_token_with_secret(
    service_tokens_client: ServiceTokensClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    account_id = seed_service_account()["id"]

    resp = service_tokens_client.create_token(
        **create_token_body(
            account_id, name="  spec-audit padded  ", expiryDays=MAX_EXPIRY_DAYS
        )
    )
    assert resp.status_code == 201, resp.text[:500]
    body = resp.json()
    token = body["token"]
    try:
        assert_strict_openapi_exchange(resp, ROUTE)
        assert body["message"] == "Service token created successfully"
        assert set(token) == {
            "id",
            "name",
            "serviceAccountId",
            "scopes",
            "createdAt",
            "expiresAt",
            "accessToken",
        }
        # The validator's parsed body replaces req.body, so the name comes back trimmed.
        assert token["name"] == "spec-audit padded"
        assert token["serviceAccountId"] == account_id
        assert token["scopes"] == list(DEFAULT_TOKEN_SCOPES)
        assert token["accessToken"].startswith(SECRET_PREFIX)
        assert abs(_lifetime_days(token) - MAX_EXPIRY_DAYS) < 0.01

        listed = service_tokens_client.list_tokens(account_id)
        assert listed.status_code == 200, listed.text[:500]
        assert [item["id"] for item in listed.json()["tokens"]] == [token["id"]]
    finally:
        service_tokens_client.revoke_token(token["id"], account_id)


def test_omitted_expiry_defaults_to_90_days_and_duplicate_scopes_collapse(
    seed_service_token: SeedServiceToken,
) -> None:
    token = seed_service_token(scopes=["kb:read", "kb:read"])

    assert abs(_lifetime_days(token) - DEFAULT_EXPIRY_DAYS) < 0.01, token
    assert token["scopes"] == ["kb:read"]


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        pytest.param({"scopes": []}, "body.scopes", id="empty-scopes"),
        pytest.param({"scopes": "kb:read"}, "body.scopes", id="scopes-not-a-list"),
        pytest.param({"scopes": None}, "body.scopes", id="scopes-missing"),
        pytest.param({"name": "   "}, "body.name", id="blank-name"),
        pytest.param({"name": "x" * 101}, "body.name", id="name-over-100"),
        pytest.param({"name": None}, "body.name", id="name-missing"),
        pytest.param({"serviceAccountId": "not-an-object-id"}, "body.serviceAccountId", id="malformed-account-id"),
        pytest.param({"serviceAccountId": None}, "body.serviceAccountId", id="account-id-missing"),
        pytest.param({"expiryDays": 0}, "body.expiryDays", id="expiry-0"),
        pytest.param({"expiryDays": MAX_EXPIRY_DAYS + 1}, "body.expiryDays", id="expiry-over-365"),
        pytest.param({"expiryDays": 1.5}, "body.expiryDays", id="expiry-fraction"),
        pytest.param({"expiryDays": "30"}, "body.expiryDays", id="expiry-string"),
        pytest.param({"expiryDays": "never"}, "body.expiryDays", id="expiry-never"),
    ],
)
def test_invalid_body_is_a_validation_error(
    service_tokens_client: ServiceTokensClient,
    overrides: dict[str, Any],
    field: str,
) -> None:
    # Rejected by zod before any account lookup, so the id need not exist.
    body = create_token_body(MISSING_SERVICE_ACCOUNT_ID, **overrides)
    resp = service_tokens_client.create_token(**body)

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR", error
    assert [e["field"] for e in error["metadata"]["errors"]] == [field], error
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_without_a_body_is_a_validation_error(
    service_tokens_client: ServiceTokensClient,
) -> None:
    resp = service_tokens_client.post("")

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "scope",
    [
        pytest.param(DENIED_TOKEN_SCOPE, id="denied-scope"),
        pytest.param(UNKNOWN_TOKEN_SCOPE, id="unknown-scope"),
    ],
)
def test_create_with_a_scope_the_service_refuses_is_rejected(
    service_tokens_client: ServiceTokensClient,
    seed_service_account: SeedServiceAccount,
    scope: str,
) -> None:
    account_id = seed_service_account()["id"]

    resp = service_tokens_client.create_token(**create_token_body(account_id, scopes=[scope]))
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] != "VALIDATION_ERROR"
    assert scope in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)

    listed = service_tokens_client.list_tokens(account_id)
    assert listed.status_code == 200, listed.text[:500]
    assert listed.json()["tokens"] == []


def test_create_for_a_disabled_account_is_rejected(
    service_tokens_client: ServiceTokensClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    account_id = seed_service_account(disabled=True)["id"]

    resp = service_tokens_client.create_token(**create_token_body(account_id))
    assert resp.status_code == 400, resp.text[:500]
    assert "disabled" in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_past_the_active_token_cap_is_rejected(
    service_tokens_client: ServiceTokensClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    # The account's deletion on teardown revokes every token minted here.
    account_id = seed_service_account()["id"]
    for _ in range(MAX_ACTIVE_TOKENS):
        minted = service_tokens_client.create_token(**create_token_body(account_id))
        assert minted.status_code == 201, minted.text[:500]

    resp = service_tokens_client.create_token(**create_token_body(account_id))

    assert resp.status_code == 400, resp.text[:500]
    assert f"already holds {MAX_ACTIVE_TOKENS} active tokens" in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)
    listed = service_tokens_client.list_tokens(account_id)
    assert len(listed.json()["tokens"]) == MAX_ACTIVE_TOKENS


def test_unknown_body_fields_are_ignored(
    service_tokens_client: ServiceTokensClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    account_id = seed_service_account()["id"]

    with outside_request_contract("undocumented body fields; zod strips them"):
        resp = service_tokens_client.create_token(
            **create_token_body(account_id, userId="someone-else", refreshToken=True)
        )
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 201, resp.text[:500]
    token = resp.json()["token"]
    try:
        assert token["serviceAccountId"] == account_id
    finally:
        service_tokens_client.revoke_token(token["id"], account_id)


def test_create_for_unknown_service_account_is_not_found(
    service_tokens_client: ServiceTokensClient,
) -> None:
    resp = service_tokens_client.create_token(
        **create_token_body(MISSING_SERVICE_ACCOUNT_ID)
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_without_token_is_unauthorized(
    service_tokens_client: ServiceTokensClient,
) -> None:
    resp = service_tokens_client.create_token(
        auth=False, **create_token_body(MISSING_SERVICE_ACCOUNT_ID)
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", json=create_token_body(MISSING_SERVICE_ACCOUNT_ID))

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_with_a_token_lacking_user_invite_is_forbidden(
    service_tokens_client: ServiceTokensClient, kb_read_pat: str
) -> None:
    resp = request_with_token(
        service_tokens_client._client.base_url,
        kb_read_pat,
        "POST",
        json=create_token_body(MISSING_SERVICE_ACCOUNT_ID),
    )

    assert resp.status_code == 403, resp.text[:500]
    assert "Insufficient scope" in resp.text
    assert_strict_openapi_exchange(resp, ROUTE)
