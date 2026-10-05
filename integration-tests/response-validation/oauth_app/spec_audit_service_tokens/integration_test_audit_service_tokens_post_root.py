"""Strict OpenAPI audit of POST /api/v1/service-tokens."""

from __future__ import annotations

from datetime import datetime

import pytest
from service_tokens_audit_support import (
    DEFAULT_TOKEN_SCOPES,
    DENIED_TOKEN_SCOPE,
    MAX_EXPIRY_DAYS,
    MISSING_SERVICE_ACCOUNT_ID,
    SeedServiceAccount,
    ServiceTokensClient,
    create_token_body,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/service-tokens"
SECRET_PREFIX = "phsvc_"


def _parse(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


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
        assert_strict_openapi_response(resp, ROUTE)
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
        lifetime = _parse(token["expiresAt"]) - _parse(token["createdAt"])
        assert abs(lifetime.total_seconds() - MAX_EXPIRY_DAYS * 86400) < 60

        listed = service_tokens_client.list_tokens(account_id)
        assert listed.status_code == 200, listed.text[:500]
        assert [item["id"] for item in listed.json()["tokens"]] == [token["id"]]
    finally:
        service_tokens_client.revoke_token(token["id"], account_id)


def test_create_with_empty_scopes_fails_validation(
    service_tokens_client: ServiceTokensClient,
) -> None:
    # Rejected by zod before any account lookup, so the id need not exist.
    resp = service_tokens_client.create_token(
        **create_token_body(MISSING_SERVICE_ACCOUNT_ID, scopes=[])
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_create_with_denied_scope_is_rejected(
    service_tokens_client: ServiceTokensClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    account_id = seed_service_account()["id"]

    resp = service_tokens_client.create_token(
        **create_token_body(account_id, scopes=[DENIED_TOKEN_SCOPE])
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    listed = service_tokens_client.list_tokens(account_id)
    assert listed.status_code == 200, listed.text[:500]
    assert listed.json()["tokens"] == []


def test_create_for_unknown_service_account_is_not_found(
    service_tokens_client: ServiceTokensClient,
) -> None:
    resp = service_tokens_client.create_token(
        **create_token_body(MISSING_SERVICE_ACCOUNT_ID)
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_create_without_token_is_unauthorized(
    service_tokens_client: ServiceTokensClient,
) -> None:
    resp = service_tokens_client.create_token(
        auth=False, **create_token_body(MISSING_SERVICE_ACCOUNT_ID)
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
