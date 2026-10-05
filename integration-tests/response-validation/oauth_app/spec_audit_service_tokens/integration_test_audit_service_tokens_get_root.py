"""Strict OpenAPI audit of GET /api/v1/service-tokens.

Chain: authenticate -> userAdminCheck -> requireScopes(user:read) -> zod query -> listTokens.
"""

from __future__ import annotations

import pytest
from service_tokens_audit_support import (
    MALFORMED_SERVICE_ACCOUNT_ID,
    MISSING_SERVICE_ACCOUNT_ID,
    SeedServiceToken,
    ServiceTokensClient,
    request_as,
)
from strict_openapi import assert_strict_openapi_response

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/service-tokens"
LISTED_TOKEN_FIELDS = {"id", "name", "serviceAccountId", "scopes", "createdAt", "expiresAt"}


def test_list_returns_the_accounts_tokens_without_the_secret(
    service_tokens_client: ServiceTokensClient, seed_service_token: SeedServiceToken
) -> None:
    token = seed_service_token()

    resp = service_tokens_client.list_tokens(token["serviceAccountId"])

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert set(body) == {"tokens"}, body.keys()
    assert [row["id"] for row in body["tokens"]] == [token["id"]]
    row = body["tokens"][0]
    assert LISTED_TOKEN_FIELDS <= set(row), row.keys()
    assert "accessToken" not in row
    assert row["serviceAccountId"] == token["serviceAccountId"]
    assert row["name"] == token["name"]
    assert row["scopes"] == token["scopes"]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_without_token_is_unauthorized(service_tokens_client: ServiceTokensClient) -> None:
    resp = service_tokens_client.list_tokens(MISSING_SERVICE_ACCOUNT_ID, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_as_non_admin_is_forbidden_before_validation(second_user: SecondUser) -> None:
    # userAdminCheck is router-level, so the malformed id never reaches zod.
    resp = request_as(
        second_user, "GET", params={"serviceAccountId": MALFORMED_SERVICE_ACCOUNT_ID}
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_without_service_account_id_is_rejected(
    service_tokens_client: ServiceTokensClient,
) -> None:
    resp = service_tokens_client.list_tokens()

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_for_unknown_service_account_is_not_found(
    service_tokens_client: ServiceTokensClient,
) -> None:
    resp = service_tokens_client.list_tokens(MISSING_SERVICE_ACCOUNT_ID)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
