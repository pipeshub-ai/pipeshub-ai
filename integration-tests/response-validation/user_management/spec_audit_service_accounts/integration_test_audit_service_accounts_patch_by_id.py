"""Strict OpenAPI audit of PATCH /api/v1/service-accounts/:id."""

from __future__ import annotations

from typing import Any

import pytest
from service_accounts_audit_support import (
    BY_ID_TEMPLATE,
    MALFORMED_SERVICE_ACCOUNT_ID,
    MISSING_SERVICE_ACCOUNT_ID,
    SeedServiceAccount,
    ServiceAccountsClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/service-accounts/:id"
assert ROUTE == BY_ID_TEMPLATE


def test_patch_updates_fields_and_returns_account(
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    account = seed_service_account()
    changes: dict[str, Any] = {
        "fullName": "  Spec audit renamed  ",
        "description": "patched by the spec audit",
        "isDisabled": True,
    }

    resp = service_accounts_client.update(account["id"], json=changes)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    updated = resp.json()
    assert updated["id"] == account["id"]
    assert updated["slug"] == account["slug"]
    assert updated["email"] == account["email"]
    assert updated["fullName"] == "Spec audit renamed"
    assert updated["description"] == "patched by the spec audit"
    assert updated["isDisabled"] is True

    # Enabling takes a separate conditional-write path in the service.
    enabled = service_accounts_client.update(account["id"], json={"isDisabled": False})
    assert enabled.status_code == 200, enabled.text[:500]
    assert_strict_openapi_response(enabled, ROUTE)
    assert enabled.json()["isDisabled"] is False
    assert enabled.json()["fullName"] == "Spec audit renamed"


@pytest.mark.parametrize(
    ("account_id", "body"),
    [
        pytest.param(MALFORMED_SERVICE_ACCOUNT_ID, {"fullName": "x"}, id="malformed-id"),
        # Validation runs before the lookup, so an unknown id still yields 400 here.
        pytest.param(MISSING_SERVICE_ACCOUNT_ID, {}, id="empty-body"),
    ],
)
def test_patch_invalid_request_is_rejected(
    service_accounts_client: ServiceAccountsClient,
    account_id: str,
    body: dict[str, Any],
) -> None:
    resp = service_accounts_client.update(account_id, json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_patch_unknown_id_is_not_found(
    service_accounts_client: ServiceAccountsClient,
) -> None:
    resp = service_accounts_client.update(
        MISSING_SERVICE_ACCOUNT_ID, json={"fullName": "nobody"}
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_patch_as_member_is_forbidden(
    second_user: SecondUser,
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    account = seed_service_account()

    resp = request_as(
        second_user, "PATCH", f"/{account['id']}", json={"fullName": "hijacked"}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    after = service_accounts_client.fetch(account["id"])
    assert after.status_code == 200, after.text[:500]
    assert after.json()["fullName"] == account["fullName"]
