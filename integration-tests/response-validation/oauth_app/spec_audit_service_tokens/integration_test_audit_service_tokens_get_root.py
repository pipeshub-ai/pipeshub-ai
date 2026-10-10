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
    SeedServiceAccount,
    request_as,
    request_with_token,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

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
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_without_token_is_unauthorized(service_tokens_client: ServiceTokensClient) -> None:
    resp = service_tokens_client.list_tokens(MISSING_SERVICE_ACCOUNT_ID, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_as_non_admin_is_forbidden_before_validation(second_user: SecondUser) -> None:
    # userAdminCheck is router-level, so the malformed id never reaches zod.
    resp = request_as(
        second_user, "GET", params={"serviceAccountId": MALFORMED_SERVICE_ACCOUNT_ID}
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "service_account_id",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty"),
        pytest.param(MALFORMED_SERVICE_ACCOUNT_ID, id="malformed"),
    ],
)
def test_list_without_a_valid_service_account_id_is_rejected(
    service_tokens_client: ServiceTokensClient, service_account_id: str | None
) -> None:
    resp = service_tokens_client.list_tokens(service_account_id)

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR", error
    assert [e["field"] for e in error["metadata"]["errors"]] == ["query.serviceAccountId"], error
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_still_works_for_a_disabled_account(
    service_tokens_client: ServiceTokensClient,
    seed_service_token: SeedServiceToken,
) -> None:
    token = seed_service_token()
    account_id = token["serviceAccountId"]
    disabled = service_tokens_client._client.request(
        "PATCH", f"/api/v1/service-accounts/{account_id}", json={"isDisabled": True}
    )
    assert disabled.status_code == 200, disabled.text[:500]

    resp = service_tokens_client.list_tokens(account_id)

    assert resp.status_code == 200, resp.text[:500]
    assert [row["id"] for row in resp.json()["tokens"]] == [token["id"]]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_for_an_account_with_no_tokens_is_empty(
    service_tokens_client: ServiceTokensClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    resp = service_tokens_client.list_tokens(seed_service_account()["id"])

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {"tokens": []}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_ignores_unknown_query_parameters(
    service_tokens_client: ServiceTokensClient, seed_service_token: SeedServiceToken
) -> None:
    token = seed_service_token()

    with outside_request_contract("an undocumented query parameter; zod strips it"):
        resp = service_tokens_client.list_tokens(token["serviceAccountId"], limit="0")
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert [row["id"] for row in resp.json()["tokens"]] == [token["id"]]


def test_list_with_a_token_lacking_user_read_is_forbidden(
    service_tokens_client: ServiceTokensClient, kb_read_pat: str
) -> None:
    resp = request_with_token(
        service_tokens_client._client.base_url,
        kb_read_pat,
        "GET",
        params={"serviceAccountId": MISSING_SERVICE_ACCOUNT_ID},
    )

    assert resp.status_code == 403, resp.text[:500]
    assert "Insufficient scope" in resp.text
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_for_unknown_service_account_is_not_found(
    service_tokens_client: ServiceTokensClient,
) -> None:
    resp = service_tokens_client.list_tokens(MISSING_SERVICE_ACCOUNT_ID)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
