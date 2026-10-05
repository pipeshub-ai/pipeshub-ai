"""Strict OpenAPI audit of GET /api/v1/service-accounts.

Chain: authenticate -> rate limiter -> requireScopes(user:read) -> userAdminCheck -> list.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from service_accounts_audit_support import (
    ROOT_TEMPLATE,
    SERVICE_ACCOUNT_EMAIL_DOMAIN,
    SeedServiceAccount,
    ServiceAccountsClient,
    request_as,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in rows}


def test_list_returns_seeded_accounts_newest_first(
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    older = seed_service_account(description="spec audit listing")
    newer = seed_service_account(disabled=True)

    resp = service_accounts_client.list()

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert set(body) == {"serviceAccounts"}, body.keys()
    rows = body["serviceAccounts"]
    ids = [row["id"] for row in rows]
    assert older["id"] in ids and newer["id"] in ids
    assert ids.index(newer["id"]) < ids.index(older["id"])

    listed = _by_id(rows)
    listed_older, listed_newer = listed[older["id"]], listed[newer["id"]]
    assert listed_older["slug"] == older["slug"]
    assert listed_older["fullName"] == older["fullName"]
    assert listed_older["description"] == "spec audit listing"
    assert listed_older["isDisabled"] is False
    assert listed_older["email"].endswith(f"@{SERVICE_ACCOUNT_EMAIL_DOMAIN}")
    assert listed_newer["isDisabled"] is True
    assert "description" not in listed_newer
    assert_strict_openapi_response(resp, ROOT_TEMPLATE)


def test_list_omits_deleted_account(
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    kept = seed_service_account()
    deleted = seed_service_account()
    removed = service_accounts_client.remove(deleted["id"])
    assert removed.status_code == 204, removed.text[:500]

    resp = service_accounts_client.list()

    assert resp.status_code == 200, resp.text[:500]
    ids = [row["id"] for row in resp.json()["serviceAccounts"]]
    assert kept["id"] in ids
    assert deleted["id"] not in ids
    assert_strict_openapi_response(resp, ROOT_TEMPLATE)


def test_list_without_token_is_unauthorized(
    service_accounts_client: ServiceAccountsClient,
) -> None:
    resp = service_accounts_client.list(auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROOT_TEMPLATE)


def test_list_as_non_admin_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET")

    assert resp.status_code == 403, resp.text[:500]
    assert "serviceAccounts" not in resp.text
    assert_strict_openapi_response(resp, ROOT_TEMPLATE)
