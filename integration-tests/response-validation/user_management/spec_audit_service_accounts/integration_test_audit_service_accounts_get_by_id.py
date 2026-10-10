"""Strict OpenAPI audit of GET /api/v1/service-accounts/:id."""

from __future__ import annotations

import pytest
from service_accounts_audit_support import (
    MALFORMED_SERVICE_ACCOUNT_ID,
    MISSING_SERVICE_ACCOUNT_ID,
    SERVICE_ACCOUNT_EMAIL_DOMAIN,
    SeedServiceAccount,
    ServiceAccountsClient,
    request_as,
    request_with_token,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/service-accounts/:id"


def test_get_returns_the_seeded_account(
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    account = seed_service_account(description="Spec audit get-by-id")

    resp = service_accounts_client.fetch(account["id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body == account
    assert body["description"] == "Spec audit get-by-id"
    assert body["isDisabled"] is False
    assert body["email"].endswith(f"@{SERVICE_ACCOUNT_EMAIL_DOMAIN}")


def test_get_unknown_or_human_id_is_not_found(
    service_accounts_client: ServiceAccountsClient,
    second_user: SecondUser,
) -> None:
    resp = service_accounts_client.fetch(MISSING_SERVICE_ACCOUNT_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    # kind: 'service' is part of the lookup, so a real human user's id must not resolve.
    human = service_accounts_client.fetch(second_user.user_id)
    assert human.status_code == 404, human.text[:500]
    assert_strict_openapi_exchange(human, ROUTE)


def test_get_malformed_id_is_rejected(
    service_accounts_client: ServiceAccountsClient,
) -> None:
    resp = service_accounts_client.fetch(MALFORMED_SERVICE_ACCOUNT_ID)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_as_member_is_forbidden(
    second_user: SecondUser,
    seed_service_account: SeedServiceAccount,
) -> None:
    account = seed_service_account()

    resp = request_as(second_user, "GET", f"/{account['id']}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_without_token_is_unauthorized(
    service_accounts_client: ServiceAccountsClient,
) -> None:
    resp = service_accounts_client.fetch(MISSING_SERVICE_ACCOUNT_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_with_a_token_lacking_user_read_is_forbidden(
    service_accounts_client: ServiceAccountsClient, kb_read_pat: str
) -> None:
    resp = request_with_token(
        service_accounts_client._client.base_url, kb_read_pat, "GET", f"/{MISSING_SERVICE_ACCOUNT_ID}"
    )
    assert resp.status_code == 403, resp.text[:500]
    assert "Insufficient scope" in resp.text
    assert_strict_openapi_exchange(resp, ROUTE)
