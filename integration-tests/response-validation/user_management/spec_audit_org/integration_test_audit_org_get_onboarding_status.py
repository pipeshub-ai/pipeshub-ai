"""Strict OpenAPI audit of GET /api/v1/org/onboarding-status."""

from __future__ import annotations

from typing import Any

import pytest
from bson import ObjectId
from helper.clients.org_client import OrgClient
from helper.second_user import SecondUser
from org_audit_support import (
    INVALID_BEARER,
    ONBOARDING_ROUTE,
    ONBOARDING_STATUSES,
    ORG_COLLECTION,
    ScopedCaller,
    error_of,
    request_as,
)
from pymongo.database import Database
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit


def test_admin_reads_the_status(org_client: OrgClient, org_intact: str) -> None:
    resp = org_client.get_onboarding_status()
    assert resp.status_code == 200, resp.text[:300]
    assert_strict_openapi_exchange(resp, ONBOARDING_ROUTE)
    assert resp.json()["status"] in ONBOARDING_STATUSES


def test_member_reads_the_status(second_user: SecondUser, org_intact: str) -> None:
    resp = request_as(second_user, "GET", "/onboarding-status")
    assert resp.status_code == 200, resp.text[:300]
    assert_strict_openapi_exchange(resp, ONBOARDING_ROUTE)


def test_unset_status_reads_as_not_configured(
    org_client: OrgClient, onboarding_restored: str, org_db: Database[Any]
) -> None:
    org_db[ORG_COLLECTION].update_one({"_id": ObjectId(onboarding_restored)}, {"$unset": {"onBoardingStatus": ""}})
    with outside_request_contract("no validator: proves an unknown query parameter is ignored"):
        resp = org_client.get("/onboarding-status", params={"refresh": "1"})
        assert resp.status_code == 200, resp.text[:300]
        assert_strict_openapi_exchange(resp, ONBOARDING_ROUTE)
    assert resp.json() == {"status": "notConfigured"}


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(org_client: OrgClient, headers: dict[str, str]) -> None:
    resp = org_client.get("/onboarding-status", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:300]
    assert_strict_openapi_exchange(resp, ONBOARDING_ROUTE)


def test_oauth_token_without_org_read_is_forbidden(narrow_scope: ScopedCaller) -> None:
    resp = narrow_scope("GET", "/onboarding-status")
    assert resp.status_code == 403, resp.text[:300]
    assert_strict_openapi_exchange(resp, ONBOARDING_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: org:read"
