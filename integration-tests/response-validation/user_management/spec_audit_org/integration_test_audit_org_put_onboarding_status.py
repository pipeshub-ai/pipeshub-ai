"""Strict OpenAPI audit of PUT /api/v1/org/onboarding-status (restored after every test)."""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.org_client import OrgClient
from helper.second_user import SecondUser
from org_audit_support import (
    INVALID_BEARER,
    ONBOARDING_ROUTE,
    ONBOARDING_STATUSES,
    ScopedCaller,
    error_of,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize("status", ONBOARDING_STATUSES)
def test_admin_sets_each_status(org_client: OrgClient, onboarding_restored: str, status: str) -> None:
    resp = org_client.update_onboarding_status(status=status)
    assert resp.status_code == 200, resp.text[:300]
    assert_strict_openapi_exchange(resp, ONBOARDING_ROUTE)
    assert resp.json() == {"message": "Onboarding status updated successfully", "status": status}
    assert org_client.get_onboarding_status().json()["status"] == status


def test_unknown_fields_are_dropped(org_client: OrgClient, onboarding_restored: str) -> None:
    with outside_request_contract("proves fields outside the schema pass the validator and are dropped"):
        resp = org_client.put("/onboarding-status", json={"status": "skipped", "step": 3}, params={"x": "1"})
        assert resp.status_code == 200, resp.text[:300]
        assert_strict_openapi_exchange(resp, ONBOARDING_ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-status"),
        pytest.param({"status": "done"}, id="unknown-status"),
        pytest.param({"status": True}, id="status-not-string"),
    ],
)
def test_invalid_body_fails_validation(org_client: OrgClient, onboarding_restored: str, body: Any) -> None:
    resp = org_client.put("/onboarding-status", json=body)
    assert resp.status_code == 400, resp.text[:300]
    assert_strict_openapi_exchange(resp, ONBOARDING_ROUTE)
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR"
    assert "body.status" in {e["field"] for e in error["metadata"]["errors"]}, error


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(org_client: OrgClient, headers: dict[str, str]) -> None:
    resp = org_client.put("/onboarding-status", auth=False, headers=headers, json={"status": "skipped"})
    assert resp.status_code == 401, resp.text[:300]
    assert_strict_openapi_exchange(resp, ONBOARDING_ROUTE)


@pytest.mark.parametrize(
    "body",
    [pytest.param({"status": "skipped"}, id="valid-body"), pytest.param({"status": "done"}, id="invalid-body")],
)
def test_member_is_forbidden_before_the_body_is_validated(
    second_user: SecondUser, onboarding_restored: str, body: Any
) -> None:
    resp = request_as(second_user, "PUT", "/onboarding-status", json=body)
    assert resp.status_code == 403, resp.text[:300]
    assert_strict_openapi_exchange(resp, ONBOARDING_ROUTE)


def test_oauth_token_without_org_write_is_forbidden(narrow_scope: ScopedCaller, onboarding_restored: str) -> None:
    resp = narrow_scope("PUT", "/onboarding-status", json={"status": "skipped"})
    assert resp.status_code == 403, resp.text[:300]
    assert_strict_openapi_exchange(resp, ONBOARDING_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: org:write"
