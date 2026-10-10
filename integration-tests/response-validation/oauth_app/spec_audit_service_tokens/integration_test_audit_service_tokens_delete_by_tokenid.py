"""Strict OpenAPI audit of DELETE /api/v1/service-tokens/:tokenId."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from service_tokens_audit_support import (
    MALFORMED_TOKEN_ID,
    MISSING_SERVICE_ACCOUNT_ID,
    MISSING_TOKEN_ID,
    SeedServiceToken,
    ServiceTokensClient,
    request_as,
    request_with_token,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/service-tokens/:tokenId"


def test_revoke_removes_token_then_reports_not_found(
    service_tokens_client: ServiceTokensClient,
    seed_service_token: SeedServiceToken,
) -> None:
    token = seed_service_token()
    token_id, account_id = token["id"], token["serviceAccountId"]

    resp = service_tokens_client.revoke_token(token_id, account_id)
    assert resp.status_code == 204, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.content == b""

    listed = service_tokens_client.list_tokens(account_id)
    assert listed.status_code == 200, listed.text[:500]
    assert token_id not in [item["id"] for item in listed.json()["tokens"]]

    again = service_tokens_client.revoke_token(token_id, account_id)
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)


def test_revoke_without_token_is_unauthorized(
    service_tokens_client: ServiceTokensClient,
) -> None:
    resp = service_tokens_client.revoke_token(
        MISSING_TOKEN_ID, MISSING_SERVICE_ACCOUNT_ID, auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_revoke_as_member_is_forbidden_and_leaves_token(
    service_tokens_client: ServiceTokensClient,
    second_user: SecondUser,
    seed_service_token: SeedServiceToken,
) -> None:
    token = seed_service_token()
    token_id, account_id = token["id"], token["serviceAccountId"]

    resp = request_as(
        second_user, "DELETE", f"/{token_id}", params={"serviceAccountId": account_id}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    listed = service_tokens_client.list_tokens(account_id)
    assert listed.status_code == 200, listed.text[:500]
    assert token_id in [item["id"] for item in listed.json()["tokens"]]


@pytest.mark.parametrize(
    ("token_id", "service_account_id", "field"),
    [
        pytest.param(MALFORMED_TOKEN_ID, MISSING_SERVICE_ACCOUNT_ID, "params.tokenId", id="malformed-token-id"),
        pytest.param(MISSING_TOKEN_ID, None, "query.serviceAccountId", id="missing-service-account-id"),
        pytest.param(MISSING_TOKEN_ID, "not-an-object-id", "query.serviceAccountId", id="malformed-service-account-id"),
    ],
)
def test_revoke_with_invalid_ids_is_bad_request(
    service_tokens_client: ServiceTokensClient,
    token_id: str,
    service_account_id: str | None,
    field: str,
) -> None:
    resp = service_tokens_client.revoke_token(token_id, service_account_id)
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR", error
    assert [e["field"] for e in error["metadata"]["errors"]] == [field], error
    assert_strict_openapi_exchange(resp, ROUTE)


def test_revoke_for_an_unknown_account_is_not_found(
    service_tokens_client: ServiceTokensClient,
) -> None:
    resp = service_tokens_client.revoke_token(MISSING_TOKEN_ID, MISSING_SERVICE_ACCOUNT_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert "Service account not found" in resp.text
    assert_strict_openapi_exchange(resp, ROUTE)


def test_revoke_under_another_account_is_not_found_and_leaves_token(
    service_tokens_client: ServiceTokensClient,
    seed_service_token: SeedServiceToken,
) -> None:
    token = seed_service_token()
    other_account = seed_service_token()["serviceAccountId"]

    resp = service_tokens_client.revoke_token(token["id"], other_account)
    assert resp.status_code == 404, resp.text[:500]
    assert "Service token not found" in resp.text
    assert_strict_openapi_exchange(resp, ROUTE)

    listed = service_tokens_client.list_tokens(token["serviceAccountId"])
    assert token["id"] in [item["id"] for item in listed.json()["tokens"]]


def test_revoke_works_while_the_account_is_disabled(
    service_tokens_client: ServiceTokensClient,
    seed_service_token: SeedServiceToken,
) -> None:
    token = seed_service_token()
    account_id = token["serviceAccountId"]
    disabled = service_tokens_client._client.request(
        "PATCH", f"/api/v1/service-accounts/{account_id}", json={"isDisabled": True}
    )
    assert disabled.status_code == 200, disabled.text[:500]

    resp = service_tokens_client.revoke_token(token["id"], account_id)
    assert resp.status_code == 204, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_revoke_with_a_token_lacking_user_delete_is_forbidden(
    service_tokens_client: ServiceTokensClient, kb_read_pat: str
) -> None:
    resp = request_with_token(
        service_tokens_client._client.base_url,
        kb_read_pat,
        "DELETE",
        f"/{MISSING_TOKEN_ID}",
        params={"serviceAccountId": MISSING_SERVICE_ACCOUNT_ID},
    )
    assert resp.status_code == 403, resp.text[:500]
    assert "Insufficient scope" in resp.text
    assert_strict_openapi_exchange(resp, ROUTE)
