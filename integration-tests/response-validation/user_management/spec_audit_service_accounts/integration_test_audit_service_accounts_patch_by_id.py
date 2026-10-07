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
    request_with_token,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

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
    assert_strict_openapi_exchange(resp, ROUTE)
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
    assert_strict_openapi_exchange(enabled, ROUTE)
    assert enabled.json()["isDisabled"] is False
    assert enabled.json()["fullName"] == "Spec audit renamed"


@pytest.mark.parametrize(
    ("account_id", "body", "field"),
    [
        pytest.param(MALFORMED_SERVICE_ACCOUNT_ID, {"fullName": "x"}, "params.id", id="malformed-id"),
        # Validation runs before the lookup, so an unknown id still yields 400 here.
        pytest.param(MISSING_SERVICE_ACCOUNT_ID, {}, "body", id="empty-body"),
        # Unknown fields are stripped before the "at least one field" refine runs.
        pytest.param(MISSING_SERVICE_ACCOUNT_ID, {"slug": "renamed"}, "body", id="only-unknown-fields"),
        pytest.param(MISSING_SERVICE_ACCOUNT_ID, {"fullName": "   "}, "body.fullName", id="blank-full-name"),
        pytest.param(MISSING_SERVICE_ACCOUNT_ID, {"fullName": "x" * 101}, "body.fullName", id="full-name-over-100"),
        pytest.param(MISSING_SERVICE_ACCOUNT_ID, {"description": "d" * 501}, "body.description", id="description-over-500"),
        pytest.param(MISSING_SERVICE_ACCOUNT_ID, {"isDisabled": "true"}, "body.isDisabled", id="is-disabled-string"),
    ],
)
def test_patch_invalid_request_is_rejected(
    service_accounts_client: ServiceAccountsClient,
    account_id: str,
    body: dict[str, Any],
    field: str,
) -> None:
    resp = service_accounts_client.update(account_id, json=body)
    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR", error
    assert [e["field"] for e in error["metadata"]["errors"]] == [field], error
    assert_strict_openapi_exchange(resp, ROUTE)


def test_patch_without_a_body_is_a_validation_error(
    service_accounts_client: ServiceAccountsClient,
) -> None:
    resp = service_accounts_client.update(MISSING_SERVICE_ACCOUNT_ID)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_patch_lengths_are_counted_after_trimming_and_description_may_be_emptied(
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    account = seed_service_account(description="to be cleared")

    resp = service_accounts_client.update(
        account["id"], json={"fullName": "   " + "n" * 100 + " ", "description": "   "}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["fullName"] == "n" * 100
    assert resp.json()["description"] == ""


def test_patch_ignores_unknown_fields_next_to_a_known_one(
    service_accounts_client: ServiceAccountsClient,
    seed_service_account: SeedServiceAccount,
) -> None:
    account = seed_service_account()

    with outside_request_contract("an undocumented body field; zod strips it"):
        resp = service_accounts_client.update(
            account["id"], json={"fullName": "Spec audit kept", "slug": "renamed", "role": "admin"}
        )
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json()["slug"] == account["slug"]
    assert resp.json()["fullName"] == "Spec audit kept"


def test_patch_without_token_is_unauthorized(
    service_accounts_client: ServiceAccountsClient,
) -> None:
    resp = service_accounts_client.update(
        MISSING_SERVICE_ACCOUNT_ID, auth=False, json={"fullName": "nobody"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_patch_with_a_token_lacking_user_write_is_forbidden(
    service_accounts_client: ServiceAccountsClient, kb_read_pat: str
) -> None:
    resp = request_with_token(
        service_accounts_client._client.base_url,
        kb_read_pat,
        "PATCH",
        f"/{MISSING_SERVICE_ACCOUNT_ID}",
        json={"fullName": "nobody"},
    )
    assert resp.status_code == 403, resp.text[:500]
    assert "Insufficient scope" in resp.text
    assert_strict_openapi_exchange(resp, ROUTE)


def test_patch_unknown_id_is_not_found(
    service_accounts_client: ServiceAccountsClient,
) -> None:
    resp = service_accounts_client.update(
        MISSING_SERVICE_ACCOUNT_ID, json={"fullName": "nobody"}
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


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
    assert_strict_openapi_exchange(resp, ROUTE)

    after = service_accounts_client.fetch(account["id"])
    assert after.status_code == 200, after.text[:500]
    assert after.json()["fullName"] == account["fullName"]
