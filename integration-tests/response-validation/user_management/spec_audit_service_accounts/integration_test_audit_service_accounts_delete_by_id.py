"""Strict OpenAPI audit of DELETE /api/v1/service-accounts/:id."""

from __future__ import annotations

import pytest
from service_accounts_audit_support import (
    MALFORMED_SERVICE_ACCOUNT_ID,
    SeedServiceAccount,
    ServiceAccountsClient,
    request_as,
    request_with_token,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/service-accounts/:id"


def test_delete_removes_account_then_reports_not_found(
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    account_id = seed_service_account()["id"]

    resp = service_accounts_client.remove(account_id)
    assert resp.status_code == 204, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.content == b""

    fetched = service_accounts_client.fetch(account_id)
    assert fetched.status_code == 404, fetched.text[:500]

    listed = service_accounts_client.list()
    assert listed.status_code == 200, listed.text[:500]
    assert account_id not in [a["id"] for a in listed.json()["serviceAccounts"]]

    again = service_accounts_client.remove(account_id)
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)


def test_delete_as_member_is_forbidden_and_keeps_account(
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
    second_user: SecondUser,
) -> None:
    account_id = seed_service_account()["id"]

    resp = request_as(second_user, "DELETE", f"/{account_id}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    fetched = service_accounts_client.fetch(account_id)
    assert fetched.status_code == 200, fetched.text[:500]


def test_delete_without_token_is_unauthorized_and_keeps_account(
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    account_id = seed_service_account()["id"]

    resp = service_accounts_client.remove(account_id, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    fetched = service_accounts_client.fetch(account_id)
    assert fetched.status_code == 200, fetched.text[:500]


def test_delete_malformed_id_is_rejected(
    service_accounts_client: ServiceAccountsClient,
) -> None:
    resp = service_accounts_client.remove(MALFORMED_SERVICE_ACCOUNT_ID)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_human_user_id_is_not_found(
    service_accounts_client: ServiceAccountsClient,
    second_user: SecondUser,
) -> None:
    # The lookup is scoped to kind=service, so this route must not reach a human user.
    resp = service_accounts_client.remove(second_user.user_id)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_with_a_token_lacking_user_delete_is_forbidden_and_keeps_account(
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
    kb_read_pat: str,
) -> None:
    account_id = seed_service_account()["id"]

    resp = request_with_token(
        service_accounts_client._client.base_url, kb_read_pat, "DELETE", f"/{account_id}"
    )
    assert resp.status_code == 403, resp.text[:500]
    assert "Insufficient scope" in resp.text
    assert_strict_openapi_exchange(resp, ROUTE)

    fetched = service_accounts_client.fetch(account_id)
    assert fetched.status_code == 200, fetched.text[:500]
